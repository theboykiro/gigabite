"""Shared test harness. Import this FIRST, before anything from `gigabite`.

    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _harness                                  # noqa: F401 (import order)

The two-line preamble in each test file exists because `unittest discover -s tests`
puts `tests/` on `sys.path` but `unittest tests.test_x` does not, and both have to
keep working. A `tests/__init__.py` would fix the first and break the second.

Two jobs, and the second is the one that matters.

**Redirect every store into a temp directory.** `gigabite.config` reads its
environment variables once, at import, and caches the results into module-level
constants. So the first test module to import the package decides where the whole
session's stores live, and every later module's `os.environ[...]` line is a no-op
against constants that were already computed. Setting the environment here, before
any test module can reach `gigabite`, makes that a single deterministic decision
instead of an import-order race.

The variables are assigned unconditionally rather than with `setdefault`, so an
environment that already points at a real knowledge base cannot leak the user's
own data into a test run.

**Restore the config globals after every test.** Because those constants are
cached, a test that needs its own knowledge root has to rebind them, and anything
that rebinds a module global has to put it back — a missed restore surfaces as
confusing failures in a *different* file, minutes later, and only under
`unittest discover`. `TempRoot` does the rebinding and the restoring in one place
so that no individual test has to remember.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Before `gigabite` is importable, and before it is imported.
_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="gigabite-tests-"))
os.environ["GIGABITE_CORE_DIR"] = str(_SESSION_ROOT / "core")
os.environ["GIGABITE_KNOWLEDGE_DIR"] = str(_SESSION_ROOT / "knowledge")

# HOME too, for the whole run and every subprocess it starts. Several paths are
# derived from it rather than from the two variables above — ~/.claude/projects,
# ~/Library/LaunchAgents, the keychain account's defaults — and a test that
# reached any of them would read or write the real user's.
_FAKE_HOME = _SESSION_ROOT / "home"
_FAKE_HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(_FAKE_HOME)

# No test may reach the real keychain — not even a test of the code that talks to
# it. Every `security` invocation made in-process (subprocess.run/call/check_output
# all build a Popen) raises instead of running, so a test that forgets to mock fails
# loudly rather than quietly reading, or prompting for, the user's secrets. A test
# that needs a stored key patches the reader (e.g. `granola_live.read_token`); a
# test of the reader itself patches `subprocess.run` and asserts the command.
import subprocess  # noqa: E402

_REAL_POPEN = subprocess.Popen


class KeychainAccessInTest(AssertionError):
    """Raised when a test would have run the macOS `security` tool for real."""


def _is_security(args) -> bool:
    argv0 = args if isinstance(args, (str, bytes)) else (args[0] if args else "")
    if isinstance(argv0, bytes):
        argv0 = argv0.decode(errors="replace")
    words = str(argv0).split()
    return bool(words) and os.path.basename(words[0]) == "security"


_KEYCHAIN_ATTEMPTS = []


class _NoKeychainPopen(_REAL_POPEN):
    def __init__(self, args, *a, **kw):
        if _is_security(args):
            _KEYCHAIN_ATTEMPTS.append(args)
            raise KeychainAccessInTest(
                "a test tried to run the macOS keychain tool: %r. Mock the credential "
                "reader (or subprocess.run) instead — see tests/_harness.py." % (args,))
        super().__init__(args, *a, **kw)


subprocess.Popen = _NoKeychainPopen


def _fail_the_run_if_the_keychain_was_reached():
    """Loud even when the code under test swallowed the exception: the run as a
    whole exits non-zero, whatever the individual tests reported."""
    if _KEYCHAIN_ATTEMPTS:
        sys.stderr.write("\nFAILED: %d attempt(s) to run the macOS keychain tool from a "
                         "test (tests/_harness.py):\n" % len(_KEYCHAIN_ATTEMPTS))
        for args in _KEYCHAIN_ATTEMPTS:
            sys.stderr.write("  %r\n" % (args,))
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


import atexit  # noqa: E402
atexit.register(_fail_the_run_if_the_keychain_was_reached)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gigabite import config  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402

# Every config constant derived from KNOWLEDGE_DIR or CORE_DIR at import time.
# Rebound per test and restored afterwards. If a new derived constant is added to
# config.py and a code path under test reads it, it belongs in this list — the
# failure mode otherwise is a test quietly reaching the real ~/Knowledge.
_DERIVED = (
    "KNOWLEDGE_DIR", "CORE_DIR", "CORE_FILE", "MACHINE_DIR", "INDEX_DIR", "DB_PATH",
    "SOURCES_DIR", "SOURCES_CLAUDE_AI", "SOURCES_MEETINGS",
    "ORIGINALS_DIR", "ALIASES_FILE",
    "BINDINGS_FILE",
    "CLAUDE_CODE_PROJECTS_DIR",
)


class TempRoot(unittest.TestCase):
    """Base case: a private knowledge root and `~/.core` per test.

    Subclasses get `self.root` (the knowledge base), `self.core`, and `self.db`,
    and may override `setUp` as long as they call `super().setUp()` first.
    """

    def setUp(self):
        super().setUp()
        self._saved_env = {k: os.environ.get(k)
                           for k in ("GIGABITE_KNOWLEDGE_DIR", "GIGABITE_CORE_DIR")}
        self._saved_cfg = {n: getattr(config, n) for n in _DERIVED if hasattr(config, n)}
        self.addCleanup(self._restore)

        base = Path(tempfile.mkdtemp(prefix="gigabite-case-"))
        self.root = base / "knowledge"
        self.core = base / "core"
        self.root.mkdir(parents=True, exist_ok=True)
        self.core.mkdir(parents=True, exist_ok=True)
        self.db = base / "index.db"

        os.environ["GIGABITE_KNOWLEDGE_DIR"] = str(self.root)
        os.environ["GIGABITE_CORE_DIR"] = str(self.core)
        _repoint(self.root, self.core)

    def _restore(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for name, value in self._saved_cfg.items():
            setattr(config, name, value)

    # -- conveniences --------------------------------------------------------

    def fresh_store(self, db=None) -> Store:
        """A second Store on the same file, as a later `gigabite ingest` would."""
        return Store(connect(Path(db) if db else self.db))

    def files(self, pattern="*.md"):
        """Visible files under the knowledge root — machinery excluded."""
        return sorted(p.relative_to(self.root).as_posix()
                      for p in self.root.rglob(pattern)
                      if config.MACHINE_DIRNAME not in p.parts)


def _repoint(knowledge: Path, core: Path) -> None:
    """Re-derive every cached constant against these roots."""
    config.KNOWLEDGE_DIR = knowledge
    config.CORE_DIR = core
    config.CORE_FILE = core / "core.md"
    config.MACHINE_DIR = config.machine_dir()
    config.INDEX_DIR = config.MACHINE_DIR / "index"
    config.DB_PATH = config.INDEX_DIR / "gigabite.db"
    config.SOURCES_DIR = config.MACHINE_DIR / "imports"
    config.SOURCES_CLAUDE_AI = config.SOURCES_DIR / "claude_ai"
    config.SOURCES_MEETINGS = config.SOURCES_DIR / "meetings"
    config.ORIGINALS_DIR = config.MACHINE_DIR / "originals"
    config.ALIASES_FILE = config.MACHINE_DIR / "aliases.json"
    config.BINDINGS_FILE = config.MACHINE_DIR / "bindings.json"
    # NOT derived from KNOWLEDGE_DIR — it points at ~/.claude/projects, so
    # without this a test that runs `ingest` reads the user's real Claude
    # Code transcripts: slow, and it makes results depend on their history.
    config.CLAUDE_CODE_PROJECTS_DIR = knowledge.parent / "claude-projects"
    config.CLAUDE_CODE_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


# A scratch area for tests that want a Store but not a whole knowledge root —
# util, ranking and passage tests, which never touch the filesystem layout.
SCRATCH = _SESSION_ROOT / "scratch"
SCRATCH.mkdir(parents=True, exist_ok=True)


def scratch_store(name: str) -> Store:
    """A Store on its own file, rebuilt from empty each time it is asked for."""
    db = SCRATCH / f"{name}.db"
    if db.exists():
        db.unlink()
    return Store(connect(db))


# ---------------------------------------------------------------------------
# document factories
# ---------------------------------------------------------------------------

def msg(seq: int, role: str, text: str, ts: str = "") -> Message:
    return Message(seq=seq, role=role, text=text, ts_utc=ts)


def doc(source: str, native_id: str, *, title: str = "", project: str = "",
        texts=(), created: str = "", updated: str = "", ref: str = "",
        **extra) -> Document:
    """A Document with one message per string in `texts`, roles alternating."""
    return Document(
        source=source, native_id=native_id, title=title or native_id, project=project,
        created_utc=created, updated_utc=updated or created, ref=ref,
        messages=[msg(i, "user" if i % 2 == 0 else "assistant", t)
                  for i, t in enumerate(texts)],
        **extra,
    )


__all__ = ["TempRoot", "doc", "msg", "Document", "Message", "Store", "connect", "config"]
