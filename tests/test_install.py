"""Tests for `install.sh`, run for real against a throwaway HOME.

Two properties are pinned here, and they pull against each other.

**The output has to fit on a screen.** `gigabite welcome`, at the end of step 7, is
the only part of an install written for a person to read. Everything above it is
bookkeeping, and a first-time reader who has scrolled past thirty green ticks has
already spent the attention that screen needed. So on the success path the installer
prints one line per numbered step, and the per-item detail moves behind `--verbose`.
The bound is asserted rather than described, because "it looked short enough" is not
a property that a loop reintroduced next month would violate loudly.

**Quietening must never hide something the user needs to know.** The line that says
`kept your own /search — gigabite did not write that file` is the entire point of the
logic above it, and it is asserted in the QUIET run specifically: a bound on line
count is easy to meet by suppressing the wrong lines.

Nothing here may touch the real home directory. The subprocess environment is built
from scratch rather than inherited (the `env -i` rule), `HOME` points at a temp
directory, and the two seams that would otherwise reach outside it are redirected —
the same two `uninstall.sh` already names, spelled the same way:

* `GIGABITE_BIN_DIRS` — otherwise the installer symlinks into `/opt/homebrew/bin`
  or `/usr/local/bin`, i.e. over the launcher of whoever is running the suite.
* `GIGABITE_LAUNCHCTL` — otherwise `launchctl bootstrap` loads the fake HOME's plist
  into the real user's launchd domain, pointing their daily job at a temp directory
  that is about to be deleted. Pointed at a recorder script, so the calls are
  asserted rather than made.

A run of install.sh takes about two seconds, so a class whose assertions are all
about the same install shares one — `install_once` — rather than paying for a fresh
one per assertion.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"
UNINSTALL = REPO / "uninstall.sh"

# The step whose output is the payload: everything above it is what got quietened,
# and everything from it down (the ingest summary, then `welcome`) is left alone.
INDEX_STEP = "6/7"
# `welcome`'s first line, in the only state a fresh fake HOME can be in: nothing
# indexed, because there is nothing on this machine to index.
WELCOME_FIRST = "gigabite is installed"

# Measured, not guessed. A quiet install of a fresh HOME prints 12 lines before the
# index step — five of them the step headings themselves, which stay by design:
#
#   1/7 heading, store paths
#   2/7 heading, linked + PATH, "open a new terminal" (the way to use what was made)
#   3/7 heading, "5 commands, 3 subagents, 3 skills"
#   4/7 heading, router + hook (with the way to switch the hook off)
#   5/7 heading, scheduled, "disable with: launchctl bootout ..."
#
# The budget is one line above that. Anything that reintroduces a per-item loop adds
# three or more and fails here, which is the point of a number rather than a
# description. Raising it is a decision about a first-time reader's attention, so it
# should be made deliberately, in a commit that says so.
QUIET_PREAMBLE_MAX = 13
# The same count taken to `welcome`'s first line, so the step 6 ingest summary and
# the step 7 heading are inside the bound too — that is the scroll a new user
# actually does before reaching the screen written for them.
QUIET_TO_WELCOME_MAX = 24


def strip_ansi(text: str) -> str:
    """Colour codes off, so an assertion can be written the way the line reads."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\033":
            while i < len(text) and text[i] != "m":
                i += 1
            i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


class Sandbox:
    """A throwaway HOME with both seams pointed inside it.

    Not a TestCase, so a class can hold one for the whole class and a single test
    can hold its own — the sandbox owns the temp directory either way.
    """

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="gigabite-install-"))
        self.home = self.base / "home"
        self.bin_dir = self.base / "bin"          # stands in for /opt/homebrew/bin
        self.home.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.launchctl_log = self.base / "launchctl.log"
        self.launchctl = self.base / "launchctl"
        self.launchctl.write_text(
            '#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % self.launchctl_log,
            encoding="utf-8")
        self.launchctl.chmod(0o755)

    def destroy(self):
        shutil.rmtree(str(self.base), True)

    def write(self, rel: str, text: str) -> Path:
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run(self, *args, extra_env=None):
        """`env -i HOME=<tmp> ./install.sh <args>` — nothing inherited."""
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            # The script shells out to /usr/bin/python3; without this it litters the
            # fake HOME with a bytecode cache.
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIGABITE_BIN_DIRS": str(self.bin_dir),
            "GIGABITE_LAUNCHCTL": str(self.launchctl),
        }
        env.update(extra_env or {})
        proc = subprocess.run(["/bin/bash", str(INSTALL)] + list(args),
                              input="", env=env, cwd=str(REPO),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
        return proc.returncode, strip_ansi(proc.stdout + proc.stderr)


@unittest.skipUnless(Path("/bin/bash").exists() and Path("/usr/bin/python3").exists(),
                     "install.sh is bash + /usr/bin/python3 by construction")
class InstallCase(unittest.TestCase):
    """One way to run the installer, and one way to read what it printed."""

    @classmethod
    def install_once(cls, *args, **kwargs):
        """A sandbox and its install, shared by every test in the class."""
        sandbox = Sandbox()
        cls.addClassCleanup(sandbox.destroy)
        code, out = sandbox.run(*args, **kwargs)
        assert code == 0, "install.sh exited %s\n%s" % (code, out)
        return sandbox, out

    def setUp(self):
        super().setUp()
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.destroy)

    def install(self, *args, expect=0, **kwargs):
        code, out = self.sandbox.run(*args, **kwargs)
        self.assertEqual(expect, code, "exit %s\n%s" % (code, out))
        return out

    # -- reading the output --------------------------------------------------

    def preamble(self, out: str):
        """The lines printed before the index step — what quiet mode governs."""
        return self._before(out, lambda line: line.startswith(INDEX_STEP), INDEX_STEP)

    def to_welcome(self, out: str):
        return self._before(out, lambda line: WELCOME_FIRST in line, WELCOME_FIRST)

    def _before(self, out, matches, what):
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if matches(line):
                return lines[:i]
        self.fail("never reached %r:\n%s" % (what, out))


# ---------------------------------------------------------------------------
class TestQuietByDefault(InstallCase):
    """One install of a fresh HOME, and everything its output has to be."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox_, cls.out = cls.install_once()

    def test_the_preamble_before_the_index_step_is_within_budget(self):
        pre = self.preamble(self.out)
        self.assertLessEqual(
            len(pre), QUIET_PREAMBLE_MAX,
            "%d lines before %s, budget %d:\n%s"
            % (len(pre), INDEX_STEP, QUIET_PREAMBLE_MAX, "\n".join(pre)))

    def test_everything_before_the_welcome_screen_fits_on_a_screen(self):
        pre = self.to_welcome(self.out)
        self.assertLessEqual(
            len(pre), QUIET_TO_WELCOME_MAX,
            "%d lines before the welcome screen, budget %d:\n%s"
            % (len(pre), QUIET_TO_WELCOME_MAX, "\n".join(pre)))

    def test_all_seven_numbered_steps_still_announce_themselves(self):
        for step in ("1/7", "2/7", "3/7", "4/7", "5/7", "6/7", "7/7"):
            self.assertIn(step, self.out)

    def test_the_per_item_confirmations_are_collapsed_into_a_count(self):
        self.assertIn("5 commands, 3 subagents, 3 skills", self.out)
        for gone in ("/search-status", "subagent gg-builder", "skill meeting-prep",
                     "seeded .core/core.md"):
            self.assertNotIn(gone, "\n".join(self.preamble(self.out)),
                             "per-item line still printed in the quiet run: %s" % gone)

    def test_it_still_says_how_to_switch_off_the_things_that_run_by_themselves(self):
        """A scheduled job, and a hook on every prompt. Both need a way out, and a
        user who cannot see one has to go looking for it in someone else's script."""
        self.assertIn("disable with: launchctl bootout", self.out)
        self.assertIn("settings.json to disable", self.out)

    def test_the_welcome_screen_is_untouched(self):
        self.assertIn(WELCOME_FIRST, self.out)
        self.assertIn("gigabite search", self.out)

    def test_the_ingest_summary_is_untouched(self):
        for source in ("Claude Code", "Claude.ai", "Meeting", "Note"):
            self.assertIn(source, self.out)
        self.assertIn("document(s) added/updated", self.out)


# ---------------------------------------------------------------------------
class TestVerboseRestoresTheDetail(InstallCase):
    """The flag is not decoration: it is what a maintainer asks a colleague to run
    when their install misbehaved, so it has to bring back the per-item lines."""

    @classmethod
    def setUpClass(cls):
        cls.quiet_sandbox, cls.quiet = cls.install_once()
        cls.loud_sandbox, cls.loud = cls.install_once("--verbose")

    def test_verbose_prints_materially_more_than_the_quiet_run(self):
        quiet, loud = len(self.preamble(self.quiet)), len(self.preamble(self.loud))
        self.assertGreater(loud, quiet + 9,
                           "verbose added only %d lines" % (loud - quiet))

    def test_verbose_names_every_file_it_wrote(self):
        for item in ("/gg", "/search-status", "subagent gg-builder",
                     "skill meeting-prep", "seeded .core/core.md",
                     "seeded Knowledge/README.md"):
            self.assertIn(item, self.loud)

    def test_the_environment_variable_is_honoured_too(self):
        """So it can be turned on for a run nobody is typing — a bootstrap, a
        colleague pasting one line, a CI job."""
        out = self.install(extra_env={"GIGABITE_VERBOSE": "1"})
        self.assertIn("subagent gg-builder", out)

    def test_a_mistyped_flag_is_refused_rather_than_ignored(self):
        """The failure this flag exists to prevent is a quiet install that the
        person who ran it believed was verbose."""
        out = self.install("--verbse", expect=1)
        self.assertIn("unknown option: --verbse", out)
        self.assertFalse((self.sandbox.home / ".claude").exists(), "installed anyway")


# ---------------------------------------------------------------------------
class TestQuieteningHidesNothingThatMatters(InstallCase):
    """Every line below is one that a bound on line count would be easy to meet by
    dropping. Each is asserted in the QUIET run, which is the only run that matters
    here — nobody is reading verbosely yet the first time they install something."""

    def test_a_command_of_the_users_own_is_reported_as_kept_in_the_quiet_run(self):
        # No mention of gigabite anywhere in it: that is the test install.sh applies,
        # and a fixture that said "no gigabite marker" would be claimed as ours.
        mine = self.sandbox.write(".claude/commands/search.md",
                                  "# my own search command\nnothing of theirs here\n")
        digest = hashlib.sha256(mine.read_bytes()).hexdigest()
        out = self.install()
        self.assertIn("kept your own /search", out)
        self.assertEqual(digest, hashlib.sha256(mine.read_bytes()).hexdigest(),
                         "overwrote a file gigabite did not write")
        # And the count tells the truth about it rather than claiming five.
        self.assertIn("4 commands, 3 subagents, 3 skills", out)

    def test_an_agent_of_the_users_own_is_reported_as_kept_in_the_quiet_run(self):
        mine = self.sandbox.write(".claude/agents/gg-builder.md",
                                  "# my builder\nno marker\n")
        out = self.install()
        self.assertIn("kept your own subagent gg-builder", out)
        self.assertEqual("# my builder\nno marker\n", mine.read_text(encoding="utf-8"))

    def test_a_replaced_readme_says_so_and_says_where_the_old_text_went(self):
        """The one place the installer replaces a file rather than keeping it. It
        takes a copy first, and a backup nobody was told about is a backup nobody
        will find."""
        self.sandbox.write("Knowledge/README.md", "an old map of folders that moved\n")
        out = self.install()
        self.assertIn("was out of date", out)
        self.assertIn("old text kept as", out)
        kept = list((self.sandbox.home / "Knowledge/.gigabite/originals")
                    .glob("replaced-*"))
        self.assertEqual(1, len(kept), kept)
        self.assertIn("an old map", kept[0].read_text(encoding="utf-8"))

    def test_it_says_so_when_it_could_not_find_a_writable_path_directory(self):
        out = self.install(extra_env={"GIGABITE_BIN_DIRS": "/dev/null/nowhere"})
        self.assertIn("couldn't write to a PATH dir", out)


# ---------------------------------------------------------------------------
class TestItIsIdempotent(InstallCase):
    """The header promises "re-run any time", and a second run is the common case:
    a colleague re-runs after `git pull`. It must not raise its voice for that."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox_, _first = cls.install_once()
        code, cls.second = cls.sandbox_.run()
        assert code == 0, cls.second

    def test_a_second_quiet_run_succeeds_and_says_nothing_alarming(self):
        pre = "\n".join(self.preamble(self.second))
        for alarm in ("!", "✗", "error", "Traceback"):
            self.assertNotIn(alarm, pre, "second run complained:\n%s" % self.second)
        self.assertIn(WELCOME_FIRST, self.second)

    def test_a_second_quiet_run_is_no_longer_than_the_first(self):
        self.assertLessEqual(len(self.preamble(self.second)), QUIET_PREAMBLE_MAX)

    def test_a_second_run_does_not_stack_a_second_path_line_into_the_shell_rc(self):
        for rc in (".zshrc", ".bash_profile"):
            text = (self.sandbox_.home / rc).read_text(encoding="utf-8")
            self.assertEqual(1, text.count("added by gigabite"), rc)

    def test_a_second_run_does_not_stack_a_second_router_block(self):
        text = (self.sandbox_.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("<!-- gigabite:router:start -->"))


# ---------------------------------------------------------------------------
class TestTheSeamsAreRealSeams(InstallCase):
    """Without these two the script cannot be tested at all, and the cost of that
    is not a missing test — it is that the only way to find out what install.sh
    does is to let it do it to your own machine."""

    def test_the_launcher_is_linked_into_the_directory_it_was_pointed_at(self):
        self.install()
        link = self.sandbox.bin_dir / "gigabite"
        self.assertTrue(link.is_symlink(), "no launcher in GIGABITE_BIN_DIRS")
        self.assertEqual(str(REPO / "bin" / "gigabite"), os.readlink(str(link)))

    def test_it_writes_nothing_into_the_real_path_directories(self):
        """Read-only on the real machine: whatever is in those directories now,
        including the owner's own launcher, is exactly there afterwards."""
        defaults = ["/opt/homebrew/bin", "/usr/local/bin",
                    str(Path.home() / ".local/bin"), str(Path.home() / "bin")]

        def snapshot():
            out = {}
            for d in defaults:
                p = Path(d) / "gigabite"
                if p.is_symlink():
                    out[d] = ("link", os.readlink(str(p)), p.lstat().st_mtime)
                elif p.exists():
                    out[d] = ("file", p.stat().st_size, p.stat().st_mtime)
                else:
                    out[d] = ("absent",)
            return out

        before = snapshot()
        self.install()
        self.assertEqual(before, snapshot(),
                         "the install reached outside GIGABITE_BIN_DIRS")

    def test_the_scheduled_job_is_loaded_through_the_named_launchctl(self):
        self.install()
        calls = self.sandbox.launchctl_log.read_text(encoding="utf-8")
        self.assertIn("bootout gui/%d/com.gigabite.synthesis" % os.getuid(), calls)
        self.assertIn("bootstrap gui/%d" % os.getuid(), calls)
        # And at the plist inside the fake HOME, not the real user's.
        self.assertIn(str(self.sandbox.home / "Library/LaunchAgents"), calls)

    def test_no_launchctl_is_invoked_except_through_the_seam(self):
        """One missed call site is enough to unload the real user's job, and it
        would only ever show up on the machine of whoever ran the suite."""
        for line in INSTALL.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "launchctl" not in stripped:
                continue
            if 'LAUNCHCTL="${GIGABITE_LAUNCHCTL:-launchctl}"' in stripped:
                continue
            # The only other mention is the line telling the user what to type.
            self.assertIn("disable with:", stripped,
                          "install.sh calls launchctl directly: %s" % stripped)

    def test_the_defaults_are_spelled_exactly_as_uninstall_spells_them(self):
        """Two scripts that disagree about where the launcher lives cannot uninstall
        what the other installed, and the seam would be the thing hiding it."""
        def defaults(path):
            return {line.split("=", 1)[0]: line
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.startswith(("BIN_DIRS=", "LAUNCHCTL="))}

        self.assertEqual(defaults(UNINSTALL), defaults(INSTALL))
        self.assertEqual(2, len(defaults(INSTALL)))


if __name__ == "__main__":
    unittest.main()
