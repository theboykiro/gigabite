"""`gigabite check`: one line per link in the recall chain, read-only, never a crash.

The hooks never fail a turn, so a broken install shows only as recall going quiet.
These pin what the teammate pastes back: each broken link named with its fix, a
clean install exiting 0, and a check that raises becoming a line, not a traceback.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import TempRoot  # noqa: E402,F401  (must precede any gigabite import)

from gigabite import cli, config  # noqa: E402
from gigabite.store import Store, connect  # noqa: E402

from _harness import doc  # noqa: E402


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class CheckCase(TempRoot):
    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp(prefix="gigabite-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.claude = self.home / ".claude"
        (self.claude / "projects").mkdir(parents=True)
        self.launcher = self.home / "bin" / "gigabite"
        self.launcher.parent.mkdir()
        self.launcher.write_text("#!/bin/sh\n")
        patches = (mock.patch.object(cli.Path, "home", return_value=self.home),
                   mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.claude)}),
                   mock.patch.object(cli.config, "CLAUDE_CODE_PROJECTS_DIR",
                                     self.claude / "projects"),
                   mock.patch.object(cli.osutil, "launcher", return_value=str(self.launcher)),
                   mock.patch.object(cli.osutil, "WINDOWS", False),
                   mock.patch.object(cli.shutil, "which", return_value=None))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def install(self, names=("recall", "refresh", "agent-model"), launcher=None):
        """settings.json + wrappers in the macOS shape the installer writes."""
        wrappers = self.claude / "gigabite"
        wrappers.mkdir(exist_ok=True)
        hooks = {}
        for name in names:
            script = wrappers / f"gg-{name}.sh"
            body = "#!/bin/sh\n"
            if name != "agent-model":
                body += f'GIGABITE_BIN="{launcher or self.launcher}"\n'
            script.write_text(body)
            hooks[name] = [{"hooks": [{"type": "command", "command": str(script)}]}]
        (self.claude / "settings.json").write_text(json.dumps({"hooks": hooks}))

    def index(self):
        store = Store(connect(config.DB_PATH))
        store.upsert_document(doc("note", "a", title="a note", texts=["hello there"]))
        store.commit()
        store.conn.close()

    def refreshed(self, line):
        config.MACHINE_DIR.mkdir(parents=True, exist_ok=True)
        (config.MACHINE_DIR / "refresh.log").write_text(line + "\n")

    def checks(self):
        code, out = run("check", "--json")
        return code, {r["check"]: r for r in json.loads(out)["checks"]}


class TestCheck(CheckCase):
    def healthy(self):
        self.install()
        self.index()
        from datetime import datetime
        self.refreshed(f"{datetime.now():%Y-%m-%d %H:%M:%S}  refresh done (exit 0)")

    def test_a_healthy_install_exits_zero(self):
        self.healthy()
        code, out = run("check")
        self.assertEqual(code, 0, out)
        self.assertIn("all OK", out)
        self.assertNotIn("✗", out)

    def test_a_missing_hook_is_named_with_a_fix(self):
        self.healthy()
        self.install(names=("recall", "refresh"))
        code, rows = self.checks()
        self.assertEqual(code, 1)
        self.assertFalse(rows["Claude Code hooks"]["ok"])
        self.assertIn("agent-model", rows["Claude Code hooks"]["detail"])
        self.assertIn("install", rows["Claude Code hooks"]["fix"])

    def test_hooks_pointing_at_a_deleted_launcher_fail(self):
        self.healthy()
        self.install(launcher=self.home / "gone" / "gigabite")
        code, rows = self.checks()
        self.assertEqual(code, 1)
        self.assertIn("does not exist", rows["gigabite command"]["detail"])

    def test_a_failed_refresh_fails(self):
        self.healthy()
        self.refreshed("2026-01-01 10:00:00  refresh done (exit 2)")
        rows = self.checks()[1]
        self.assertFalse(rows["last refresh"]["ok"])
        self.assertIn("exit 2", rows["last refresh"]["detail"])

    def test_a_stale_refresh_fails_and_shows_its_age(self):
        self.healthy()
        self.refreshed("2020-01-01 10:00:00  refresh done (exit 0)")
        rows = self.checks()[1]
        self.assertFalse(rows["last refresh"]["ok"])
        self.assertIn("days ago", rows["last refresh"]["detail"])

    def test_no_index_fails_and_is_not_created(self):
        self.install()
        rows = self.checks()[1]
        self.assertFalse(rows["index"]["ok"])
        self.assertFalse(config.DB_PATH.exists())
        self.assertFalse(config.MACHINE_DIR.exists())

    def test_an_unknown_claude_version_is_not_a_failure(self):
        self.healthy()
        code, rows = self.checks()
        self.assertEqual(code, 0)
        self.assertIsNone(rows["Claude Code version"]["ok"])

    def test_a_check_that_raises_becomes_one_line(self):
        self.healthy()
        with mock.patch.object(cli, "_CHECKS",
                               (("boom", mock.Mock(side_effect=RuntimeError("kaput"))),)):
            code, out = run("check")
        self.assertEqual(code, 1)
        self.assertIn("couldn't check: RuntimeError: kaput", out)

    def test_a_stale_launchd_job_is_folded_in(self):
        self.healthy()
        import plistlib
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        with (agents / "com.gigabite.old.plist").open("wb") as fh:
            plistlib.dump({"Label": "com.gigabite.old",
                           "ProgramArguments": [str(self.home / "nope")]}, fh)
        code, rows = self.checks()
        self.assertEqual(code, 1)
        self.assertIn("com.gigabite.old", rows["scheduled jobs"]["detail"])

    def test_windows_exec_hooks_are_recognised(self):
        self.index()
        exe = self.home / "bin" / "gigabite.exe"
        exe.write_text("")
        hooks = {n: [{"hooks": [{"type": "command", "command": str(exe), "args": ["hook", n]}]}]
                 for n in ("recall", "refresh", "agent-model")}
        (self.claude / "settings.json").write_text(json.dumps({"hooks": hooks}))
        with mock.patch.object(cli.osutil, "launcher", return_value=str(exe)):
            rows = self.checks()[1]
        self.assertTrue(rows["Claude Code hooks"]["ok"], rows)
        self.assertTrue(rows["gigabite command"]["ok"], rows)

    def test_status_points_at_check(self):
        self.install(names=("recall",))
        self.assertIn("gigabite check", run("status")[1])


if __name__ == "__main__":
    import unittest
    unittest.main()
