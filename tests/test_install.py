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

* `GIGABITE_BIN_DIRS` — the installer links into `~/.local/bin` by default, which
  is inside the fake HOME anyway; pointed at a separate directory so a test can
  plant a foreign `gigabite` there without touching HOME.
* `GIGABITE_LAUNCHCTL` — the installer only calls launchctl to retire the old daily
  job an earlier install scheduled. Pointed at a recorder script, so the calls are
  asserted rather than made.

A run of install.sh takes about two seconds, so a class whose assertions are all
about the same install shares one — `install_once` — rather than paying for a fresh
one per assertion.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import hashlib
import json
import os
import pwd
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
INDEX_STEP = "7/8"
# `welcome`'s first line, in the only state a fresh fake HOME can be in: nothing
# indexed, because there is nothing on this machine to index.
WELCOME_FIRST = "gigabite is installed"

# Measured, not guessed. A quiet install of a fresh HOME prints 14 lines before the
# index step — six of them the step headings themselves, which stay by design:
#
#   1/8 heading, store paths
#   2/8 heading, linked + PATH, "open a new terminal" (the way to use what was made)
#   3/8 heading, "slash commands installed: 2"
#   4/8 heading, "Claude Code isn't installed yet" (true of the fake HOME), router +
#       hooks (with the way to switch the hooks off)
#   5/8 heading, the integrations menu — a note pointing at `gigabite integrations`
#       when stdin isn't a terminal (as here), the real prompt when it is
#   6/8 heading, and one line about the protocol — either "already filled in" or the
#       instruction to run /core-setup later, for the same no-terminal reason
#
# The budget is three lines above that. Anything that reintroduces a per-item loop adds
# three or more and fails here, which is the point of a number rather than a
# description. Raising it is a decision about a first-time reader's attention, so it
# should be made deliberately, in a commit that says so.
QUIET_PREAMBLE_MAX = 17
# The same count taken to `welcome`'s first line, so the step 7 ingest summary and
# the step 8 heading are inside the bound too — that is the scroll a new user
# actually does before reaching the screen written for them.
QUIET_TO_WELCOME_MAX = 28


def tree_digest(root: Path) -> dict:
    """Every path under `root` mapped to a digest of its content (links by target)."""
    out = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[key] = "link:" + os.readlink(str(path))
        elif path.is_dir():
            out[key] = "dir"
        else:
            out[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


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

    def test_all_eight_numbered_steps_still_announce_themselves(self):
        for step in ("1/8", "2/8", "3/8", "4/8", "5/8", "6/8", "7/8", "8/8"):
            self.assertIn(step, self.out)

    def test_the_per_item_confirmations_are_collapsed_into_a_count(self):
        self.assertIn("slash commands installed: 2", self.out)
        for gone in ("✓ /search", "seeded .core/core.md"):
            self.assertNotIn(gone, "\n".join(self.preamble(self.out)),
                             "per-item line still printed in the quiet run: %s" % gone)

    def test_it_still_says_how_to_switch_off_the_things_that_run_by_themselves(self):
        """A hook on every prompt and one on every session start. Both need a way
        out, and a user who cannot see one has to go looking for it in someone
        else's script."""
        self.assertIn("settings.json to disable", self.out)

    def test_a_fresh_install_schedules_nothing_with_launchd(self):
        """The daily job is retired; the refresh hook replaced it. A fresh machine
        has nothing to migrate, so launchd is never called at all."""
        self.assertFalse(self.sandbox_.launchctl_log.exists(),
                         self.sandbox_.launchctl_log.read_text(encoding="utf-8")
                         if self.sandbox_.launchctl_log.exists() else "")
        self.assertFalse((self.sandbox_.home / "Library/LaunchAgents").exists())

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
        # Six per-item lines today: two commands, two seeded files, the router
        # block and the hooks.
        self.assertGreater(loud, quiet + 5,
                           "verbose added only %d lines" % (loud - quiet))

    def test_verbose_names_every_file_it_wrote(self):
        for item in ("✓ /search", "✓ /core-setup", "seeded .core/core.md",
                     "seeded Knowledge/README.md"):
            self.assertIn(item, self.loud)

    def test_the_environment_variable_is_honoured_too(self):
        """So it can be turned on for a run nobody is typing — a bootstrap, a
        colleague pasting one line, a CI job."""
        out = self.install(extra_env={"GIGABITE_VERBOSE": "1"})
        self.assertIn("seeded .core/core.md", out)

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
        # And the count tells the truth about it rather than claiming two.
        self.assertIn("slash commands installed: 1", out)

    def test_an_agent_of_the_users_own_is_left_alone(self):
        mine = self.sandbox.write(".claude/agents/gg-builder.md",
                                  "# my builder\nno marker\n")
        out = self.install()
        self.assertNotIn("removed subagent gg-builder", out)
        self.assertEqual("# my builder\nno marker\n", mine.read_text(encoding="utf-8"))

    def test_subagents_and_skills_an_older_install_wrote_are_removed(self):
        for name in ("gg-builder", "gg-researcher", "gg-reviewer"):
            self.sandbox.write(".claude/agents/%s.md" % name, "a gigabite subagent\n")
        for name in ("meeting-prep", "decision-record", "design-critique"):
            self.sandbox.write(".claude/skills/%s/SKILL.md" % name, "a gigabite skill\n")
        extra = self.sandbox.write(".claude/skills/meeting-prep/notes.md", "mine\n")
        out = self.install()
        home = self.sandbox.home
        for name in ("gg-builder", "gg-researcher", "gg-reviewer"):
            self.assertFalse((home / (".claude/agents/%s.md" % name)).exists(), name)
            self.assertIn("removed subagent %s" % name, out)
        for name in ("decision-record", "design-critique"):
            self.assertFalse((home / (".claude/skills/%s" % name)).exists(), name)
        # A skill directory holding a file of the user's keeps the file and the folder.
        self.assertFalse((home / ".claude/skills/meeting-prep/SKILL.md").exists())
        self.assertEqual("mine\n", extra.read_text(encoding="utf-8"))

    def test_commands_an_older_install_wrote_are_removed_but_the_users_own_are_kept(self):
        retired = ("gg", "search-status", "calendar", "meeting", "recall-status", "granola")
        for name in retired:
            self.sandbox.write(".claude/commands/%s.md" % name,
                               "runs the gigabite launcher\n")
        mine = self.sandbox.write(".claude/commands/calendar.md",
                                  "# my own calendar command\nnothing of theirs here\n")
        out = self.install()
        for name in retired:
            path = self.sandbox.home / (".claude/commands/%s.md" % name)
            if name == "calendar":
                self.assertTrue(path.exists(), "removed a command gigabite did not write")
            else:
                self.assertFalse(path.exists(), "left the retired /%s behind" % name)
                self.assertIn("removed /%s" % name, out)
        self.assertIn("nothing of theirs", mine.read_text(encoding="utf-8"))

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
        # The folder's guide is the same file as docs/KNOWLEDGE.md, so they can't drift.
        self.assertEqual((REPO / "docs/KNOWLEDGE.md").read_text(encoding="utf-8"),
                         (self.sandbox.home / "Knowledge/README.md").read_text(encoding="utf-8"))

    def test_it_says_so_when_it_could_not_find_a_writable_path_directory(self):
        out = self.install(extra_env={"GIGABITE_BIN_DIRS": "/dev/null/nowhere"})
        self.assertIn("couldn't write to a PATH dir", out)


# ---------------------------------------------------------------------------
class TestItIsIdempotent(InstallCase):
    """The header promises "re-run any time", and a second run is the common case:
    a colleague re-runs after `git pull`. It must not raise its voice for that."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox_ = Sandbox()
        cls.addClassCleanup(cls.sandbox_.destroy)
        # A machine with Claude Code on it, so the one legitimate warning a fresh
        # sandbox gets ("Claude Code isn't installed yet") is not in the way.
        cls.sandbox_.write(".claude.json", "{}\n")
        code, cls.first = cls.sandbox_.run()
        assert code == 0, cls.first
        cls.snapshot = tree_digest(cls.sandbox_.home)
        code, cls.second = cls.sandbox_.run()
        assert code == 0, cls.second

    def test_a_second_run_changes_no_file_but_the_index(self):
        """Idempotent in the strict sense: every file byte-identical, the hooks
        registered once, no second backup. Only the index database may move."""
        after = tree_digest(self.sandbox_.home)
        changed = sorted(k for k in set(self.snapshot) | set(after)
                         if self.snapshot.get(k) != after.get(k)
                         and "/.gigabite/index/" not in "/" + k)
        self.assertEqual([], changed)
        cfg = json.loads((self.sandbox_.home / ".claude/settings.json").read_text())
        self.assertEqual(1, len(cfg["hooks"]["UserPromptSubmit"]))
        self.assertEqual(1, len(cfg["hooks"]["SessionStart"]))

    def test_a_second_quiet_run_succeeds_and_says_nothing_alarming(self):
        pre = "\n".join(self.preamble(self.second))
        for alarm in ("!", "✗", "error", "Traceback"):
            self.assertNotIn(alarm, pre, "second run complained:\n%s" % self.second)
        self.assertIn(WELCOME_FIRST, self.second)

    def test_a_second_quiet_run_is_no_longer_than_the_first(self):
        self.assertLessEqual(len(self.preamble(self.second)), QUIET_PREAMBLE_MAX)

    def test_a_second_run_does_not_stack_a_second_path_line_into_the_shell_rc(self):
        text = (self.sandbox_.home / ".zshrc").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("added by gigabite"))

    def test_a_second_run_does_not_stack_a_second_router_block(self):
        text = (self.sandbox_.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(1, text.count("<!-- gigabite:router:start -->"))

    def test_the_router_block_imports_the_operating_protocol(self):
        # Claude Code expands `@path` in CLAUDE.md, but not inside a code span, so the
        # import must be a bare line of its own within the managed block. Without it,
        # core.md reaches the model only when something explicitly prints it.
        text = (self.sandbox_.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        block = text[text.index("<!-- gigabite:router:start -->"):
                     text.index("<!-- gigabite:router:end -->")]
        self.assertIn("\n@~/.core/core.md\n", block)


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
        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)   # the harness fakes HOME
        defaults = ["/opt/homebrew/bin", "/usr/local/bin",
                    str(real_home / ".local/bin"), str(real_home / "bin")]

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

    def test_the_retired_job_is_unloaded_through_the_named_launchctl(self):
        self.sandbox.write("Library/LaunchAgents/com.gigabite.synthesis.plist", "<plist/>\n")
        self.install()
        calls = self.sandbox.launchctl_log.read_text(encoding="utf-8")
        self.assertIn("bootout gui/%d/com.gigabite.synthesis" % os.getuid(), calls)
        self.assertNotIn("bootstrap", calls, "the retired job must not be loaded again")

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

    def test_uninstall_looks_everywhere_install_links(self):
        """Two scripts that disagree about where the launcher lives cannot uninstall
        what the other installed, and the seam would be the thing hiding it. The
        uninstaller's list is a superset — it also cleans up the directories older
        installs linked into — with the installer's own directory first."""
        def defaults(path):
            return {line.split("=", 1)[0]: line.split("=", 1)[1]
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.startswith(("BIN_DIRS=", "LAUNCHCTL="))}

        inst, uninst = defaults(INSTALL), defaults(UNINSTALL)
        self.assertEqual(inst["LAUNCHCTL"], uninst["LAUNCHCTL"])
        self.assertEqual('"${GIGABITE_BIN_DIRS:-$HOME/.local/bin}"', inst["BIN_DIRS"])
        self.assertTrue(uninst["BIN_DIRS"].startswith('"${GIGABITE_BIN_DIRS:-$HOME/.local/bin:'),
                        uninst["BIN_DIRS"])
        for legacy in ("/opt/homebrew/bin", "/usr/local/bin"):
            self.assertIn(legacy, uninst["BIN_DIRS"])

# ---------------------------------------------------------------------------
class TestTheHooks(InstallCase):
    """Two hooks, both registered by absolute path in the nested form Claude Code's
    settings schema uses, both installed with the launcher's path baked in."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox_, cls.out = cls.install_once()
        cls.cfg = json.loads((cls.sandbox_.home / ".claude/settings.json")
                             .read_text(encoding="utf-8"))

    def test_recall_is_on_user_prompt_submit_and_refresh_on_session_start(self):
        hook_dir = self.sandbox_.home / ".claude/gigabite"
        self.assertEqual(
            [{"hooks": [{"type": "command", "command": str(hook_dir / "gg-recall.sh")}]}],
            self.cfg["hooks"]["UserPromptSubmit"])
        self.assertEqual(
            [{"hooks": [{"type": "command", "command": str(hook_dir / "gg-refresh.sh")}]}],
            self.cfg["hooks"]["SessionStart"])

    def test_both_scripts_are_executable_and_name_the_launcher_absolutely(self):
        for name in ("gg-recall.sh", "gg-refresh.sh"):
            script = self.sandbox_.home / ".claude/gigabite" / name
            self.assertTrue(os.access(str(script), os.X_OK), name)
            text = script.read_text(encoding="utf-8")
            self.assertNotIn("__GIGABITE_BIN__", text, name)
            self.assertIn('GIGABITE_BIN="%s"' % (REPO / "bin" / "gigabite"), text)

    def test_a_users_own_hooks_are_kept_alongside(self):
        self.sandbox.write(".claude/settings.json", json.dumps({
            "model": "x",
            "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
                {"type": "command", "command": "/opt/mine/hello.sh"}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}))
        self.install()
        cfg = json.loads((self.sandbox.home / ".claude/settings.json").read_text())
        self.assertEqual("x", cfg["model"])
        self.assertEqual("say done", cfg["hooks"]["Stop"][0]["hooks"][0]["command"])
        starts = json.dumps(cfg["hooks"]["SessionStart"])
        self.assertIn("/opt/mine/hello.sh", starts)
        self.assertIn("gg-refresh.sh", starts)
        self.assertEqual(2, len(cfg["hooks"]["SessionStart"]))


# ---------------------------------------------------------------------------
class TestMigratingAnOlderInstall(InstallCase):
    """An install from before the refresh hook: the 18:00 job loaded, its log, a
    settings.json with only the recall hook, and the retired slash commands."""

    def setUp(self):
        super().setUp()
        s = self.sandbox
        s.write("Library/LaunchAgents/com.gigabite.synthesis.plist", "<plist/>\n")
        s.write("Library/Logs/gigabite-synthesis.log", "ran at 18:00\n")
        s.write("Library/LaunchAgents/com.gigabite.granola-pull.plist", "<plist/>\n")
        s.write(".claude/settings.json", json.dumps({"hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command",
                        "command": str(s.home / ".claude/gigabite/gg-recall.sh")}]}]}}))
        for name in ("gg", "search-status", "calendar", "meeting"):
            s.write(".claude/commands/%s.md" % name, "runs the gigabite launcher\n")

    def test_the_daily_job_is_unloaded_and_removed_and_the_hook_takes_over(self):
        out = self.install()
        home = self.sandbox.home
        self.assertFalse((home / "Library/LaunchAgents/com.gigabite.synthesis.plist").exists())
        self.assertFalse((home / "Library/Logs/gigabite-synthesis.log").exists())
        self.assertIn("bootout gui/%d/com.gigabite.synthesis" % os.getuid(),
                      self.sandbox.launchctl_log.read_text(encoding="utf-8"))
        self.assertIn("retired the old 18:00 daily job", out)
        cfg = json.loads((home / ".claude/settings.json").read_text())
        self.assertEqual(1, len(cfg["hooks"]["UserPromptSubmit"]), "recall doubled up")
        self.assertIn("gg-refresh.sh", json.dumps(cfg["hooks"]["SessionStart"]))
        for name in ("gg", "search-status", "calendar", "meeting"):
            self.assertFalse((home / (".claude/commands/%s.md" % name)).exists(), name)

    def test_the_optional_granola_job_is_left_alone(self):
        self.install()
        self.assertTrue((self.sandbox.home /
                         "Library/LaunchAgents/com.gigabite.granola-pull.plist").exists())
        self.assertNotIn("granola", self.sandbox.launchctl_log.read_text(encoding="utf-8"))

    def test_a_second_run_after_migrating_has_nothing_left_to_retire(self):
        self.install()
        self.sandbox.launchctl_log.unlink()
        out = self.install()
        self.assertNotIn("retired", out)
        self.assertFalse(self.sandbox.launchctl_log.exists())


# ---------------------------------------------------------------------------
class TestThePathStep(InstallCase):
    """~/.local/bin by default, and never over someone else's `gigabite`."""

    def test_it_defaults_to_local_bin_inside_home(self):
        """The real default, so the seam is left out altogether. Safe: the default
        is inside the fake HOME now, which is the point of the change."""
        env_less = self.sandbox.home / ".local/bin/gigabite"
        proc_env = {"HOME": str(self.sandbox.home), "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "GIGABITE_LAUNCHCTL": str(self.sandbox.launchctl)}
        proc = subprocess.run(["/bin/bash", str(INSTALL)], input="", env=proc_env,
                              cwd=str(REPO), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue(env_less.is_symlink())
        self.assertEqual(str(REPO / "bin" / "gigabite"), os.readlink(str(env_less)))

    def test_a_foreign_gigabite_is_never_overwritten(self):
        foreign = self.sandbox.bin_dir / "gigabite"
        foreign.write_text("#!/bin/sh\necho someone else's\n", encoding="utf-8")
        second = self.sandbox.base / "second-bin"
        out = self.install(extra_env={
            "GIGABITE_BIN_DIRS": "%s:%s" % (self.sandbox.bin_dir, second)})
        self.assertEqual("#!/bin/sh\necho someone else's\n",
                         foreign.read_text(encoding="utf-8"))
        self.assertIn("left %s alone" % foreign, out)
        self.assertTrue((second / "gigabite").is_symlink(), "fell through to the next dir")

    def test_a_foreign_symlink_is_never_overwritten_either(self):
        link = self.sandbox.bin_dir / "gigabite"
        os.symlink("/usr/bin/true", str(link))
        out = self.install()
        self.assertEqual("/usr/bin/true", os.readlink(str(link)))
        self.assertIn("left %s alone" % link, out)

    def test_a_link_into_a_gigabite_checkout_is_ours_to_refresh(self):
        link = self.sandbox.bin_dir / "gigabite"
        os.symlink(str(REPO / "bin" / "gigabite"), str(link))
        out = self.install()
        self.assertNotIn("alone", out)
        self.assertEqual(str(REPO / "bin" / "gigabite"), os.readlink(str(link)))

    def test_another_gigabite_on_path_is_reported_not_touched(self):
        other = self.sandbox.base / "elsewhere"
        other.mkdir()
        (other / "gigabite").write_text("#!/bin/sh\n", encoding="utf-8")
        (other / "gigabite").chmod(0o755)
        out = self.install(extra_env={"PATH": "%s:/usr/bin:/bin" % other})
        self.assertIn("another program called gigabite is at %s" % (other / "gigabite"), out)
        self.assertEqual("#!/bin/sh\n", (other / "gigabite").read_text(encoding="utf-8"))

    def test_bash_profile_is_not_created_and_a_new_zshrc_has_no_blank_line(self):
        self.install()
        home = self.sandbox.home
        self.assertFalse((home / ".bash_profile").exists(),
                         "a new .bash_profile would shadow the user's ~/.profile")
        text = (home / ".zshrc").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("export PATH="), repr(text))

    def test_existing_bash_files_get_the_line_after_a_separator(self):
        self.sandbox.write(".bash_profile", "export EDITOR=vim\n")
        self.install()
        text = (self.sandbox.home / ".bash_profile").read_text()
        self.assertTrue(text.startswith("export EDITOR=vim\n\nexport PATH="), repr(text))
        self.assertIn("added by gigabite", text)


# ---------------------------------------------------------------------------
class TestPreflight(InstallCase):

    def test_missing_command_line_tools_stop_it_with_the_fix(self):
        shim = self.sandbox.base / "shim"
        shim.mkdir()
        (shim / "xcode-select").write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        (shim / "xcode-select").chmod(0o755)
        out = self.install(expect=1, extra_env={"PATH": "%s:/usr/bin:/bin" % shim})
        self.assertIn("command line tools are missing", out)
        self.assertIn("xcode-select --install", out)
        self.assertFalse((self.sandbox.home / ".claude").exists(), "installed anyway")

    def test_claude_code_is_detected_by_its_own_traces_not_by_dot_claude(self):
        """install.sh creates ~/.claude itself, so a second run on a Mac without
        Claude Code must still say it is missing."""
        first = self.install()
        second = self.install()
        for out in (first, second):
            self.assertIn("Claude Code isn't installed yet", out)
        self.sandbox.write(".claude.json", "{}\n")
        self.assertNotIn("Claude Code isn't installed yet", self.install())

    def test_a_clone_under_desktop_is_warned_about(self):
        clone = self.sandbox.home / "Desktop" / "gigabite"
        for part in ("install.sh", "bin", "gigabite", "install", "docs"):
            src = REPO / part
            if src.is_dir():
                shutil.copytree(str(src), str(clone / part),
                                ignore=shutil.ignore_patterns("__pycache__"))
            else:
                clone.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(clone / part))
        env = {"HOME": str(self.sandbox.home), "PATH": "/usr/bin:/bin",
               "PYTHONDONTWRITEBYTECODE": "1",
               "GIGABITE_BIN_DIRS": str(self.sandbox.bin_dir),
               "GIGABITE_LAUNCHCTL": str(self.sandbox.launchctl)}
        proc = subprocess.run(["/bin/bash", str(clone / "install.sh")], input="",
                              env=env, cwd=str(clone), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        out = strip_ansi(proc.stdout + proc.stderr)
        self.assertEqual(0, proc.returncode, out)
        self.assertIn("macOS blocks background jobs there", out)

    def test_an_empty_claude_md_is_not_backed_up(self):
        self.install()
        self.assertFalse((self.sandbox.home / ".claude/CLAUDE.md.gigabite-bak").exists())


if __name__ == "__main__":
    unittest.main()
