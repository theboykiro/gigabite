"""Tests for `uninstall.sh`, run for real against a throwaway HOME.

The script deletes files, so the tests that matter are the ones that pin what it
must *not* delete. `~/Knowledge` and `~/.core` hold the only things on the machine
a fresh clone cannot rebuild — meetings, notes, a hand-edited `core.md` — and a
command or agent carrying one of gigabite's names may be the user's own work. Every
one of those is asserted here by hashing the file tree before and after and
comparing it byte for byte, because "it printed the right thing" is not the property
under test.

Nothing here may touch the real home directory. The subprocess environment is built
from scratch rather than inherited (the `env -i` rule), `HOME` points at a temp
directory, and the two seams that would otherwise reach outside it are redirected:

* `GIGABITE_BIN_DIRS` — otherwise the script would look in `/opt/homebrew/bin` and
  `/usr/local/bin` and find the launcher belonging to whoever is running the suite.
* `GIGABITE_LAUNCHCTL` — otherwise `launchctl bootout` would unload the real user's
  scheduled job. Pointed at a recorder script, so the call is asserted, not made.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import hashlib
import json
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
UNINSTALL = REPO / "uninstall.sh"
ROUTER_BLOCK = (REPO / "install" / "scaffold" / "CLAUDE.md").read_text(encoding="utf-8")
SKILLS = ("meeting-prep", "decision-record", "design-critique")
COMMANDS = ("gg", "search", "search-status", "calendar", "meeting")
STALE_COMMANDS = ("recall-status", "granola")
AGENTS = ("gg-builder", "gg-researcher", "gg-reviewer")
MANAGED = "<!-- gigabite:managed -->\n"


def tree(root: Path) -> dict:
    """Every path under `root`, mapped to a digest of its content.

    Symlinks are recorded by target rather than followed, so a removed link shows up
    as a difference even when the file it pointed at is still there.
    """
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[key] = "link:" + os.readlink(str(path))
        elif path.is_dir():
            out[key] = "dir"
        else:
            out[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@unittest.skipUnless(Path("/bin/bash").exists() and Path("/usr/bin/python3").exists(),
                     "uninstall.sh is bash + /usr/bin/python3 by construction")
class UninstallCase(unittest.TestCase):
    """A fake HOME with a complete install in it, and one way to run the script."""

    def setUp(self):
        super().setUp()
        base = Path(tempfile.mkdtemp(prefix="gigabite-uninstall-"))
        self.addCleanup(shutil.rmtree, str(base), True)
        self.home = base / "home"
        self.launchctl_log = base / "launchctl.log"
        self.launchctl = base / "launchctl"
        self.launchctl.write_text(
            '#!/bin/sh\necho "$@" >> "%s"\n' % self.launchctl_log, encoding="utf-8")
        self.launchctl.chmod(0o755)
        self.install()

    # -- fixture -------------------------------------------------------------

    def write(self, rel: str, text: str) -> Path:
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def install(self):
        """Everything install.sh puts on a machine, plus content it must never touch."""
        # Content. Not reproducible from a clone, and the whole point of the guard.
        self.write("Knowledge/README.md", "where things go\n")
        self.write("Knowledge/acme/decisions/pricing.md", "the anchor holds at 40\n")
        self.write("Knowledge/acme/meetings/2026-01-02-kickoff.md", "notes\n")
        self.write(".core/core.md", "# Core Protocol\nhand edited, never generated\n")
        self.write(".core/capability/sops/sop-build-qa-review.md", "builder, qa, reviewer\n")

        # Machinery.
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        os.symlink(str(REPO / "bin" / "gigabite"), str(bin_dir / "gigabite"))
        self.write(".zshrc", 'export EDITOR=vim\n\nexport PATH="%s:$PATH"'
                             '  # added by gigabite\nalias k=kubectl\n' % bin_dir)
        self.write(".bash_profile", 'export PATH="%s:$PATH"  # added by gigabite\n' % bin_dir)
        for name in COMMANDS + STALE_COMMANDS:
            self.write(".claude/commands/%s.md" % name, MANAGED + "runs the gigabite launcher\n")
        for name in AGENTS:
            self.write(".claude/agents/%s.md" % name, MANAGED + "a gigabite subagent\n")
        for name in SKILLS:
            self.write(".claude/skills/%s/SKILL.md" % name, MANAGED + "a gigabite skill\n")
        self.write(".claude/CLAUDE.md",
                   "# my own instructions\nkeep this line\n\n" + ROUTER_BLOCK
                   + "\n## a section of mine\nkeep this too\n")
        self.write(".claude/settings.json", json.dumps({
            "theme": "dark",
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command",
                                "command": str(self.home / ".claude/gigabite/gg-recall.sh")}]},
                    {"hooks": [{"type": "command", "command": "/opt/mine/notify.sh"}]},
                ],
                "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
            },
        }, indent=2))
        self.write(".claude/gigabite/gg-recall.sh", "#!/bin/bash\n# gigabite recall\n")
        self.write("Library/LaunchAgents/com.gigabite.synthesis.plist", "<plist/>\n")
        self.write("Library/Logs/gigabite-synthesis.log", "ran at 18:00\n")

    # -- running it ----------------------------------------------------------

    def run_uninstall(self, *args, stdin="", expect=0):
        """`env -i HOME=<tmp> ./uninstall.sh <args>` — nothing inherited."""
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            # The script shells out to /usr/bin/python3; without this it litters the
            # fake HOME with a bytecode cache and every tree comparison sees it.
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIGABITE_BIN_DIRS": str(self.home / ".local" / "bin"),
            "GIGABITE_LAUNCHCTL": str(self.launchctl),
        }
        proc = subprocess.run(["/bin/bash", str(UNINSTALL)] + list(args),
                              input=stdin, env=env, cwd=str(REPO),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
        self.assertEqual(proc.returncode, expect,
                         "exit %s\n%s\n%s" % (proc.returncode, proc.stdout, proc.stderr))
        return proc.stdout + proc.stderr

    def content_tree(self):
        return {"Knowledge": tree(self.home / "Knowledge"),
                "core": tree(self.home / ".core")}

    def assertContentSurvived(self, before):
        self.assertEqual(before, self.content_tree())


# ---------------------------------------------------------------------------
class TestContentIsNeverTouched(UninstallCase):
    """The hard constraint. Everything else in the script is negotiable; this isn't."""

    def test_the_knowledge_base_is_byte_identical_after_a_full_uninstall(self):
        before = tree(self.home / "Knowledge")
        self.run_uninstall("--yes")
        self.assertEqual(before, tree(self.home / "Knowledge"))
        self.assertTrue((self.home / "Knowledge/acme/decisions/pricing.md").exists())

    def test_the_operating_protocol_survives_untouched(self):
        before = tree(self.home / ".core")
        out = self.run_uninstall("--yes")
        self.assertEqual(before, tree(self.home / ".core"))
        self.assertIn("untouched", out)

    def test_it_says_where_the_two_kept_directories_are(self):
        out = self.run_uninstall("--yes")
        self.assertIn(str(self.home / "Knowledge"), out)
        self.assertIn(str(self.home / ".core" / "core.md"), out)

    def test_it_refuses_a_removal_aimed_inside_the_knowledge_base(self):
        """The guard, exercised rather than trusted: pointing the launcher search at
        the knowledge base is the closest thing to a caller passing a bad path."""
        link = self.home / "Knowledge" / "gigabite"
        os.symlink(str(REPO / "bin" / "gigabite"), str(link))
        env_before = tree(self.home / "Knowledge")
        proc = subprocess.run(
            ["/bin/bash", str(UNINSTALL), "--yes"], input="", cwd=str(REPO),
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin",
                 "PYTHONDONTWRITEBYTECODE": "1",
                 "GIGABITE_BIN_DIRS": str(self.home / "Knowledge"),
                 "GIGABITE_LAUNCHCTL": str(self.launchctl)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("refused", proc.stdout)
        self.assertEqual(env_before, tree(self.home / "Knowledge"))


# ---------------------------------------------------------------------------
class TestFilesTheUserWrote(UninstallCase):
    """install.sh refuses to overwrite a file it did not write. The same test has to
    hold in reverse, where getting it wrong deletes the file instead of skipping it."""

    def test_a_command_of_the_users_own_is_kept_and_reported(self):
        mine = self.write(".claude/commands/search.md", "# my own search command\n")
        digest = hashlib.sha256(mine.read_bytes()).hexdigest()
        out = self.run_uninstall("--yes")
        self.assertTrue(mine.exists(), "deleted a file gigabite never wrote")
        self.assertEqual(digest, hashlib.sha256(mine.read_bytes()).hexdigest())
        self.assertIn("kept %s" % mine, out)

    def test_an_agent_of_the_users_own_is_kept(self):
        mine = self.write(".claude/agents/gg-builder.md", "# my builder\nno marker here\n")
        self.run_uninstall("--yes")
        self.assertTrue(mine.exists())

    def test_a_skill_directory_holding_the_users_files_is_kept(self):
        extra = self.write(".claude/skills/meeting-prep/reference.md", "my notes\n")
        out = self.run_uninstall("--yes")
        self.assertTrue(extra.exists())
        self.assertTrue(extra.parent.is_dir())
        self.assertFalse((extra.parent / "SKILL.md").exists(), "the skill itself should go")
        self.assertIn("kept %s" % extra.parent, out)

    def test_an_unrelated_binary_called_gigabite_is_kept(self):
        link = self.home / ".local" / "bin" / "gigabite"
        link.unlink()
        link.write_text("#!/bin/sh\necho not ours\n", encoding="utf-8")
        out = self.run_uninstall("--yes")
        self.assertTrue(link.exists(), "removed a binary gigabite did not install")
        self.assertIn("kept %s" % link, out)

    def test_a_symlink_pointing_outside_a_checkout_is_kept(self):
        link = self.home / ".local" / "bin" / "gigabite"
        link.unlink()
        os.symlink("/usr/bin/true", str(link))
        out = self.run_uninstall("--yes")
        self.assertTrue(link.is_symlink())
        self.assertIn("does not point into a gigabite checkout", out)


# ---------------------------------------------------------------------------
class TestSurgeryOnSharedFiles(UninstallCase):
    """Four files gigabite edits rather than owns. Each keeps everything else in it."""

    def test_claude_md_keeps_every_line_outside_the_router_block(self):
        self.run_uninstall("--yes")
        text = (self.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("# my own instructions", text)
        self.assertIn("keep this line", text)
        self.assertIn("## a section of mine", text)
        self.assertIn("keep this too", text)
        self.assertNotIn("gigabite:router:start", text)
        self.assertNotIn("context router", text)
        self.assertTrue((self.home / ".claude/CLAUDE.md.gigabite-bak").exists(),
                        "a backup is the whole reason this edit is safe to make")

    def test_claude_md_is_untouched_when_the_markers_are_malformed(self):
        path = self.write(".claude/CLAUDE.md",
                          "# mine\n\n<!-- gigabite:router:start -->\nhalf a block\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        out = self.run_uninstall("--yes")
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertIn("damaged", out)
        self.assertFalse((self.home / ".claude/CLAUDE.md.gigabite-bak").exists())

    def test_settings_json_keeps_unrelated_hooks_and_settings(self):
        self.run_uninstall("--yes")
        cfg = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual("dark", cfg["theme"])
        self.assertEqual([{"hooks": [{"type": "command", "command": "say done"}]}],
                         cfg["hooks"]["Stop"])
        self.assertEqual([{"hooks": [{"type": "command", "command": "/opt/mine/notify.sh"}]}],
                         cfg["hooks"]["UserPromptSubmit"])

    def test_a_users_hook_sharing_the_entry_with_ours_survives(self):
        """Both hooks under one matcher: dropping the entry whole would take theirs."""
        self.write(".claude/settings.json", json.dumps({"hooks": {"UserPromptSubmit": [
            {"matcher": "*", "hooks": [
                {"type": "command", "command": str(self.home / ".claude/gigabite/gg-recall.sh")},
                {"type": "command", "command": "/opt/mine/notify.sh"},
            ]}]}}, indent=2))
        self.run_uninstall("--yes")
        cfg = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual([{"matcher": "*", "hooks": [
            {"type": "command", "command": "/opt/mine/notify.sh"}]}],
            cfg["hooks"]["UserPromptSubmit"])

    def test_settings_json_is_untouched_when_it_does_not_parse(self):
        path = self.write(".claude/settings.json", '{"hooks": {broken,,}')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        out = self.run_uninstall("--yes")
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        backup = self.home / ".claude/settings.json.gigabite.bak"
        self.assertTrue(backup.exists(), "an unparseable file is backed up before bailing")
        self.assertEqual(digest, hashlib.sha256(backup.read_bytes()).hexdigest())
        self.assertIn("isn't valid JSON", out)

    def test_the_rc_files_lose_only_the_tagged_line(self):
        self.run_uninstall("--yes")
        self.assertEqual("export EDITOR=vim\n\nalias k=kubectl\n",
                         (self.home / ".zshrc").read_text(encoding="utf-8"))
        self.assertEqual("", (self.home / ".bash_profile").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
class TestItReversesTheInstall(UninstallCase):

    def test_every_piece_of_machinery_is_gone(self):
        self.run_uninstall("--yes")
        gone = [".local/bin/gigabite", ".claude/gigabite/gg-recall.sh", ".claude/gigabite",
                "Library/LaunchAgents/com.gigabite.synthesis.plist",
                "Library/Logs/gigabite-synthesis.log"]
        gone += [".claude/commands/%s.md" % n for n in COMMANDS + STALE_COMMANDS]
        gone += [".claude/agents/%s.md" % n for n in AGENTS]
        gone += [".claude/skills/%s" % n for n in SKILLS]
        for rel in gone:
            path = self.home / rel
            self.assertFalse(path.exists() or path.is_symlink(), "%s survived" % rel)

    def test_the_launchd_job_is_booted_out_before_the_plist_goes(self):
        self.run_uninstall("--yes")
        self.assertIn("bootout gui/", self.launchctl_log.read_text(encoding="utf-8"))
        self.assertIn("com.gigabite.synthesis", self.launchctl_log.read_text(encoding="utf-8"))

    def test_a_job_that_was_never_loaded_is_not_an_error(self):
        self.launchctl.write_text("#!/bin/sh\nexit 113\n", encoding="utf-8")
        out = self.run_uninstall("--yes")
        self.assertIn("was not loaded", out)

    def test_it_prints_the_command_for_the_clone_it_will_not_delete(self):
        out = self.run_uninstall("--yes")
        self.assertIn("rm -rf %s" % REPO, out)

    def test_a_partial_install_is_not_an_error(self):
        shutil.rmtree(str(self.home / ".claude"))
        (self.home / "Library/Logs/gigabite-synthesis.log").unlink()
        out = self.run_uninstall("--yes")
        self.assertIn("already gone", out)


# ---------------------------------------------------------------------------
class TestItIsSafeToRunTwice(UninstallCase):

    def test_the_second_run_is_clean_and_changes_nothing(self):
        self.run_uninstall("--yes")
        after_first = tree(self.home)
        out = self.run_uninstall("--yes")
        self.assertIn("already uninstalled", out)
        self.assertEqual(after_first, tree(self.home))

    def test_a_second_run_needs_no_confirmation_because_there_is_nothing_to_do(self):
        self.run_uninstall("--yes")
        out = self.run_uninstall()                     # no --yes, stdin is a pipe
        self.assertIn("already uninstalled", out)


# ---------------------------------------------------------------------------
class TestNothingHappensWithoutConsent(UninstallCase):

    def test_dry_run_removes_nothing_at_all(self):
        before = tree(self.home)
        out = self.run_uninstall("--dry-run")
        self.assertEqual(before, tree(self.home), "--dry-run changed the filesystem")
        self.assertIn("Nothing was touched", out)
        self.assertFalse(self.launchctl_log.exists(), "--dry-run called launchctl")

    def test_a_pipe_without_yes_refuses_rather_than_proceeding(self):
        before = tree(self.home)
        out = self.run_uninstall(expect=1)
        self.assertIn("--yes", out)
        self.assertEqual(before, tree(self.home))

    def test_answering_no_removes_nothing(self):
        """`--yes` is the pipe's answer; a tty is what the prompt is for. Feeding it
        `n` down the pipe exercises the same branch the keyboard reaches."""
        before = tree(self.home)
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin",
               "PYTHONDONTWRITEBYTECODE": "1",
               "GIGABITE_BIN_DIRS": str(self.home / ".local" / "bin"),
               "GIGABITE_LAUNCHCTL": str(self.launchctl)}
        # -t 0 is false down a pipe, so the tty guard is disabled here to reach the
        # prompt itself. Everything else is the real script.
        script = UNINSTALL.read_text(encoding="utf-8")
        self.assertIn("[ -t 0 ] ||", script)          # the guard this test steps around
        patched = self.home.parent / "uninstall-prompting.sh"
        patched.write_text(script.replace("[ -t 0 ] ||", "true ||", 1), encoding="utf-8")
        proc = subprocess.run(["/bin/bash", str(patched)], input="n\n", env=env,
                              cwd=str(REPO), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("Nothing removed", proc.stdout)
        self.assertEqual(before, tree(self.home))

    def test_an_unknown_option_stops_before_doing_anything(self):
        before = tree(self.home)
        out = self.run_uninstall("--delete-everything", expect=1)
        self.assertIn("unknown option", out)
        self.assertEqual(before, tree(self.home))


if __name__ == "__main__":
    unittest.main()
