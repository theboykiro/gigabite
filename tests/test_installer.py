"""`gigabite install` / `gigabite uninstall`, without bash, on macOS and Windows.

test_install.py and test_uninstall.py drive the macOS wrappers and pin every line
of their output. These pin what has to hold on both platforms, through the Python
entry the Windows install uses:

* **Native**: the installed command run for real, in a subprocess, against a
  sandboxed HOME, USERPROFILE and GIGABITE_* dirs. Stores, commands, the router
  block once, the hooks once, idempotent on re-run, and an uninstall that takes
  out exactly what the install put in.
* **The Windows shape, everywhere**: `installer.WINDOWS` switched on, so the
  exec-form hooks, the skipped macOS steps and the launcher rule are tested on the
  Mac that most changes are made on, not first on a CI runner.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: E402  redirects every store into a temp dir

from gigabite import config, installer, osutil  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
START = "<!-- gigabite:router:start -->"
FAKE_EXE = "C:/Users/jane/.local/bin/gigabite.exe"
USER_SETTINGS = {
    "theme": "dark",
    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
              "SessionStart": [{"matcher": "startup", "hooks": [
                  {"type": "command", "command": "/opt/mine/hello.sh"}]}]},
}


def tree(root: Path) -> dict:
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
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def hooks_of(settings: Path) -> dict:
    cfg = json.loads(settings.read_text(encoding="utf-8"))
    return {event: [h for entry in entries for h in entry.get("hooks", [])]
            for event, entries in cfg.get("hooks", {}).items()}


def plant_user_content(home: Path, know: Path) -> None:
    """What a machine already has before gigabite: none of it may be lost."""
    (home / ".claude" / "commands").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("# my own instructions\n", encoding="utf-8")
    # In the shape gigabite writes it, so a round trip can be compared byte for byte.
    # CLAUDE.md is written in the platform's own line ends (CRLF on Windows), which
    # the router edit has to keep.
    (home / ".claude" / "settings.json").write_bytes(
        json.dumps(USER_SETTINGS, indent=2).encode("utf-8"))
    (home / ".claude" / "commands" / "mine.md").write_text("my command\n", encoding="utf-8")
    (know / "acme").mkdir(parents=True)
    (know / "acme" / "decisions.md").write_text("the anchor holds\n", encoding="utf-8")


def residue(before: dict, after: dict) -> list:
    """What an install + uninstall left changed, less what is kept by design: the
    two content stores (never touched by uninstall) and the backups it reports."""
    def kept_by_design(key):
        return (key.startswith(("Knowledge/", ".core")) or key in ("Knowledge",)
                or key.endswith((".gigabite-bak", ".gigabite.bak")))
    return sorted(k for k in set(before) | set(after)
                  if before.get(k) != after.get(k) and not kept_by_design(k))


# ---------------------------------------------------------------------------
class Sandbox:
    """A throwaway machine: HOME and USERPROFILE, the stores, and every seam."""

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="gigabite-installer-"))
        self.home = self.base / "home"
        self.know = self.home / "Knowledge"
        self.home.mkdir()
        self.bin_dir = self.base / "bin"
        self.bin_dir.mkdir()
        if os.name == "nt":
            # Not run, only found: the hooks are registered by its path. CI runs
            # the real one (.github/workflows/tests.yml).
            (self.bin_dir / "gigabite.exe").write_bytes(b"")

    def env(self, **extra) -> dict:
        env = dict(os.environ)
        for key in ("CLAUDE_CONFIG_DIR", "CLAUDECODE", "SSH_CONNECTION", "SSH_TTY",
                    "GIGABITE_VERBOSE"):
            env.pop(key, None)
        env.update({
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "GIGABITE_CORE_DIR": str(self.home / ".core"),
            "GIGABITE_KNOWLEDGE_DIR": str(self.know),
            "GIGABITE_BIN_DIRS": str(self.bin_dir),
            "GIGABITE_LAUNCHCTL": str(self.base / "no-launchctl"),
            "GIGABITE_OPEN": str(self.base / "no-opener"),        # opens nothing
            "GIGABITE_NOTIFY": "0",
            "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": (str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")
                     if os.name == "nt" else "/usr/bin:/bin"),
        })
        env.update(extra)
        return env

    def run(self, *args, **extra):
        proc = subprocess.run([sys.executable, "-m", "gigabite"] + list(args),
                              input="", env=self.env(**extra), cwd=str(self.base),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, encoding="utf-8", timeout=300)
        out = strip_ansi(proc.stdout + proc.stderr)
        assert proc.returncode == 0, "gigabite %s exited %s\n%s" % (args, proc.returncode, out)
        return out

    def destroy(self):
        shutil.rmtree(str(self.base), True)


class TestInstallAndUninstallHere(unittest.TestCase):
    """One machine taken through install, a re-run, and an uninstall."""

    @classmethod
    def setUpClass(cls):
        cls.box = box = Sandbox()
        cls.addClassCleanup(box.destroy)
        plant_user_content(box.home, box.know)
        cls.before = tree(box.home)
        cls.first = box.run("install")
        cls.installed = tree(box.home)
        cls.cfg_hooks = hooks_of(box.home / ".claude" / "settings.json")
        cls.search = (box.home / ".claude/commands/search.md").read_text(encoding="utf-8")
        cls.claude_md = (box.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        cls.second = box.run("install")
        cls.reinstalled = tree(box.home)
        cls.removal = box.run("uninstall", "--yes")
        cls.after = tree(box.home)
        cls.again = box.run("uninstall", "--yes")

    def test_the_stores_are_created(self):
        for rel in (".core/core.md", "Knowledge/README.md", "Knowledge/.gigabite/index"):
            self.assertIn(rel, self.installed, rel)

    def test_the_commands_are_copied_with_the_launcher_filled_in(self):
        for name in ("search", "core-setup"):
            self.assertIn(".claude/commands/%s.md" % name, self.installed)
        self.assertNotIn("__GIGABITE_BIN__", self.search)
        if os.name == "nt":
            # The exe, unless its path would need quoting (CI's temp dir has an 8.3
            # name with a ~), when it is the bare command uv puts on PATH.
            import shlex
            exe = (self.box.bin_dir / "gigabite.exe").as_posix()
            launcher = exe if shlex.quote(exe) == exe else "gigabite"
            self.assertIn("Bash(%s:*)" % launcher, self.search)
        else:
            self.assertIn(str(REPO / "bin" / "gigabite"), self.search)

    def test_the_router_block_is_added_once_after_the_users_own_lines(self):
        self.assertTrue(self.claude_md.startswith("# my own instructions\n"))
        self.assertEqual(1, self.claude_md.count(START))
        self.assertEqual(1, self.second.count("4/8"))

    def test_the_three_hooks_are_registered_once_beside_the_users_own(self):
        hooks = self.cfg_hooks
        mine = [h for h in hooks["SessionStart"] if h["command"] == "/opt/mine/hello.sh"]
        self.assertEqual(1, len(mine))
        self.assertEqual([{"type": "command", "command": "say done"}], hooks["Stop"])
        for event, name in (("UserPromptSubmit", "recall"), ("SessionStart", "refresh"),
                            ("PreToolUse", "agent-model")):
            ours = [h for h in hooks[event] if h is not mine[0]]
            self.assertEqual(1, len(ours), (event, hooks[event]))
            if os.name == "nt":
                self.assertEqual({"type": "command",
                                  "command": (self.box.bin_dir / "gigabite.exe").as_posix(),
                                  "args": ["hook", name]}, ours[0])
            else:
                self.assertEqual(
                    {"type": "command",
                     "command": str(self.box.home / (".claude/gigabite/gg-%s.sh" % name))},
                    ours[0])

    def test_a_re_run_changes_no_file_but_the_index(self):
        changed = sorted(k for k in set(self.installed) | set(self.reinstalled)
                         if self.installed.get(k) != self.reinstalled.get(k)
                         and "/.gigabite/index/" not in "/" + k)
        self.assertEqual([], changed)

    def test_uninstall_takes_out_exactly_what_install_put_in(self):
        self.assertEqual([], residue(self.before, self.after))

    def test_uninstall_leaves_the_users_content(self):
        for key in ("Knowledge/acme/decisions.md", ".claude/commands/mine.md"):
            self.assertEqual(self.before[key], self.after[key], key)
        self.assertIn(".core/core.md", self.after)
        cfg = json.loads((self.box.home / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(USER_SETTINGS, cfg)

    def test_a_second_uninstall_has_nothing_to_do(self):
        self.assertIn("already uninstalled", self.again)


class TestClaudeConfigDir(unittest.TestCase):
    """Claude Code reads its settings from CLAUDE_CONFIG_DIR when that is set, so
    the hooks have to go there, not into a ~/.claude it never reads."""

    def test_everything_goes_where_claude_code_reads_it(self):
        box = Sandbox()
        self.addCleanup(box.destroy)
        moved = box.base / "claude-config"
        box.run("install", CLAUDE_CONFIG_DIR=str(moved))
        self.assertEqual(3, sum(len(v) for v in hooks_of(moved / "settings.json").values()))
        self.assertTrue((moved / "commands" / "search.md").exists())
        self.assertIn(START, (moved / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertFalse((box.home / ".claude" / "settings.json").exists())
        box.run("uninstall", "--yes", CLAUDE_CONFIG_DIR=str(moved))
        self.assertFalse((moved / "settings.json").exists())

    def test_config_follows_the_variable(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/Users/jane/cc"}):
            self.assertEqual(Path("/Users/jane/cc"), config.claude_dir())
        with mock.patch.dict(os.environ):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(Path.home() / ".claude", config.claude_dir())


# ---------------------------------------------------------------------------
@contextlib.contextmanager
def windows(exe=FAKE_EXE):
    """The Windows branch of the installer, on whatever this machine is."""
    with mock.patch.object(installer, "WINDOWS", True), \
            mock.patch.object(osutil, "gigabite_exe", return_value=exe):
        yield


def windows_hook(name):
    return {"type": "command", "command": FAKE_EXE, "args": ["hook", name]}


class TestLineEnds(unittest.TestCase):
    """A CLAUDE.md written in Notepad has CRLF. The block goes in and comes out
    again without converting a line of the user's."""

    def test_crlf_survives_the_round_trip(self):
        d = Path(tempfile.mkdtemp(prefix="gigabite-eol-"))
        self.addCleanup(shutil.rmtree, str(d), True)
        path = d / "CLAUDE.md"
        mine = b"# mine\r\nkeep this\r\n"
        path.write_bytes(mine)
        block = START + "\nthe block\n<!-- gigabite:router:end -->"
        self.assertEqual("added", installer.sync_router_block(path, block))
        self.assertNotIn(b"\n", path.read_bytes().replace(b"\r\n", b""))
        self.assertEqual("current", installer.sync_router_block(path, block))
        run = installer._Uninstall()
        run.mode = "apply"
        self.assertEqual("removed", run._router(path))
        self.assertEqual(mine, path.read_bytes())


class TestWindowsHookRegistration(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gigabite-winhooks-"))
        self.addCleanup(shutil.rmtree, str(self.dir), True)
        self.settings = self.dir / "settings.json"

    def register(self, exe=FAKE_EXE):
        with windows(exe):
            return installer.register_hooks(self.settings, installer.wanted_hooks(self.dir, exe))

    def test_exec_form_no_shell_with_the_matcher_on_the_agent_guard(self):
        self.assertEqual("added:UserPromptSubmit,SessionStart,PreToolUse", self.register())
        cfg = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual({
            "UserPromptSubmit": [{"hooks": [windows_hook("recall")]}],
            "SessionStart": [{"hooks": [windows_hook("refresh")]}],
            "PreToolUse": [{"matcher": "Agent|Task", "hooks": [windows_hook("agent-model")]}],
        }, cfg["hooks"])

    def test_registered_once_however_often_it_runs(self):
        self.register()
        first = self.settings.read_bytes()
        self.assertEqual("exists", self.register())
        self.assertEqual(first, self.settings.read_bytes())

    def test_a_moved_exe_is_repointed_not_registered_twice(self):
        self.register("C:/old/gigabite.exe")
        self.register()
        hooks = hooks_of(self.settings)
        for event, name in (("UserPromptSubmit", "recall"), ("SessionStart", "refresh"),
                            ("PreToolUse", "agent-model")):
            self.assertEqual([windows_hook(name)], hooks[event])

    def test_the_users_own_hooks_and_settings_are_kept(self):
        self.settings.write_text(json.dumps(USER_SETTINGS), encoding="utf-8")
        self.register()
        cfg = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual("dark", cfg["theme"])
        self.assertEqual(USER_SETTINGS["hooks"]["Stop"], cfg["hooks"]["Stop"])
        self.assertEqual(2, len(cfg["hooks"]["SessionStart"]))

    def test_a_bom_from_windows_powershell_is_read_not_refused(self):
        self.settings.write_bytes(b"\xef\xbb\xbf" + json.dumps({"theme": "dark"}).encode())
        self.assertTrue(self.register().startswith("added:"))

    def test_unparseable_settings_are_backed_up_and_left_alone(self):
        self.settings.write_text("{broken", encoding="utf-8")
        self.assertEqual("unparseable", self.register())
        self.assertEqual("{broken", self.settings.read_text(encoding="utf-8"))
        self.assertTrue(Path(str(self.settings) + ".gigabite.bak").exists())

    def test_hooks_that_are_not_a_mapping_are_never_clobbered(self):
        self.settings.write_text(json.dumps({"hooks": ["mine"]}), encoding="utf-8")
        self.assertEqual("hooks-not-dict", self.register())
        self.assertEqual({"hooks": ["mine"]}, json.loads(self.settings.read_text()))

    def test_uninstall_removes_exec_hooks_and_keeps_a_neighbour(self):
        cfg = {"hooks": {"UserPromptSubmit": [{"hooks": [
            windows_hook("recall"), {"type": "command", "command": "C:/mine/notify.exe"}]}],
            "SessionStart": [{"hooks": [windows_hook("refresh")]}]}}
        self.assertTrue(installer.remove_hooks(cfg))
        self.assertEqual({"hooks": {"UserPromptSubmit": [{"hooks": [
            {"type": "command", "command": "C:/mine/notify.exe"}]}]}}, cfg)

    def test_a_hook_merely_named_hook_is_not_taken_for_ours(self):
        theirs = {"type": "command", "command": "C:/tools/other.exe", "args": ["hook", "recall"]}
        self.assertFalse(installer.remove_hooks({"hooks": {"Stop": [{"hooks": [theirs]}]}}))

    def test_status_sees_an_exec_form_refresh_hook(self):
        text = json.dumps({"hooks": {"SessionStart": [{"hooks": [windows_hook("refresh")]}]}})
        self.assertTrue(installer.exec_hook_registered(text, "refresh"))
        self.assertFalse(installer.exec_hook_registered(text, "recall"))
        self.assertFalse(installer.exec_hook_registered("{not json", "refresh"))


class TestWindowsInstallEndToEnd(_harness.TempRoot):
    """The whole Windows install and uninstall, in this process, on this machine."""

    def setUp(self):
        super().setUp()
        self.home = self.root.parent / "home"
        self.home.mkdir()
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home),
               "GIGABITE_OPEN": str(self.root.parent / "no-opener")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("CLAUDE_CONFIG_DIR", "CLAUDECODE", "GIGABITE_VERBOSE"):
            os.environ.pop(key, None)
        plant_user_content(self.home, self.root)

    def run_installer(self, command, *args):
        buf = io.StringIO()
        with windows(), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf), \
                mock.patch.object(installer, "_stdin_is_tty", return_value=False):
            code = installer.main(command, list(args))
        self.assertEqual(0, code, buf.getvalue())
        return buf.getvalue()

    def test_install_then_uninstall(self):
        before = tree(self.home)
        out = self.run_installer("install")
        claude = self.home / ".claude"
        hooks = hooks_of(claude / "settings.json")
        self.assertEqual([windows_hook("recall")], hooks["UserPromptSubmit"])
        self.assertEqual([windows_hook("agent-model")], hooks["PreToolUse"])
        self.assertIn(windows_hook("refresh"), hooks["SessionStart"])
        # No bash anywhere: no wrapper scripts, no rc edits, no launchd.
        self.assertFalse((claude / "gigabite").exists())
        for rc in (".zshrc", ".bash_profile", "Library"):
            self.assertFalse((self.home / rc).exists(), rc)
        self.assertIn("macOS-only, so this step is skipped on Windows", out)
        search = (claude / "commands" / "search.md").read_text(encoding="utf-8")
        self.assertIn("Bash(%s:*)" % FAKE_EXE, search)
        self.assertIn(FAKE_EXE + " search", (claude / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(1, (claude / "CLAUDE.md").read_text(encoding="utf-8").count(START))

        installed = tree(self.home)
        self.run_installer("install")
        self.assertEqual(installed, tree(self.home))

        out = self.run_installer("uninstall", "--yes")
        self.assertIn("uv tool uninstall gigabite", out)
        self.assertEqual([], residue(before, tree(self.home)))

    def test_no_exe_no_hooks(self):
        """A hook pointing at nothing fails every prompt, so none is registered."""
        buf = io.StringIO()
        with windows(exe=None), contextlib.redirect_stdout(buf), \
                mock.patch.object(installer, "_stdin_is_tty", return_value=False):
            self.assertEqual(0, installer.main("install", []))
        self.assertIn("could not find gigabite.exe, so no hooks were registered", buf.getvalue())
        cfg = json.loads((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(USER_SETTINGS, cfg)


class TestTheWindowsLauncher(unittest.TestCase):
    """What the project-binding ask and the slash commands run on Windows."""

    def test_the_exe_with_forward_slashes_and_its_suffix(self):
        with mock.patch.object(osutil, "WINDOWS", True), \
                mock.patch.object(osutil, "gigabite_exe", return_value=FAKE_EXE):
            self.assertEqual(FAKE_EXE, osutil.launcher())

    def test_a_path_that_would_need_quoting_falls_back_to_the_bare_command(self):
        spaced = "C:/Users/Jane Doe/.local/bin/gigabite.exe"
        with mock.patch.object(osutil, "WINDOWS", True), \
                mock.patch.object(osutil, "gigabite_exe", return_value=spaced):
            self.assertEqual("gigabite", osutil.launcher())

    def test_the_exe_is_found_on_path_and_its_suffix_normalised(self):
        d = Path(tempfile.mkdtemp(prefix="gigabite-exe-"))
        self.addCleanup(shutil.rmtree, str(d), True)
        exe = d / "gigabite.EXE"
        exe.write_bytes(b"")
        with mock.patch.object(shutil, "which", return_value=str(exe)):
            found = osutil.gigabite_exe()
        self.assertEqual((d / "gigabite.exe").as_posix(), found)

    def test_nothing_found_is_none_not_a_guess(self):
        with mock.patch.object(shutil, "which", return_value=None), \
                mock.patch.object(sys, "argv", ["-m"]), \
                mock.patch.object(sys, "executable", "/nowhere/python"):
            self.assertIsNone(osutil.gigabite_exe())


class TestTheCommandLine(unittest.TestCase):

    def test_a_mistyped_flag_is_refused_before_anything_is_written(self):
        box = Sandbox()
        self.addCleanup(box.destroy)
        proc = subprocess.run([sys.executable, "-m", "gigabite", "install", "--verbse"],
                              input="", env=box.env(), cwd=str(box.base),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, encoding="utf-8")
        self.assertEqual(1, proc.returncode)
        self.assertIn("unknown option: --verbse", proc.stderr)
        self.assertEqual({}, tree(box.home))

    def test_both_are_listed_in_help(self):
        from gigabite import cli
        text = cli.build_parser().format_help()
        self.assertIn("install", text)
        self.assertIn("uninstall", text)


if __name__ == "__main__":
    unittest.main()
