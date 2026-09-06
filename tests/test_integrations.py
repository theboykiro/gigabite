"""Tests for gigabite/features/integrations.py — the "AI brain" enable-flow.

Nothing here touches the real keychain or the real ~/Library/LaunchAgents:
a fake integration module stands in for `sources.granola_live` (mirroring how
`TestGranolaLive` in test_gigabite.py monkeypatches that module's functions
rather than hitting the machine), and `subprocess.run` / `Path.home` are
patched for the one test that exercises scheduling.

    python3 -m unittest discover -s tests        (from the repo root)
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  (must precede any gigabite import)

from gigabite.features import integrations  # noqa: E402


def _fake_integration_module(name: str, *, token=None):
    """A stand-in for a `sources.*_live` module: the same three-function
    shape `run_interactive` relies on, backed by a dict instead of the real
    keychain."""
    mod = types.ModuleType(name)
    state = {"token": token}

    def _store():
        state["token"] = "new-token"
        return 0

    mod.read_token = lambda: state["token"]
    mod.store_token_interactive = mock.Mock(side_effect=_store)
    mod.delete_token = lambda: state.__setitem__("token", None)
    sys.modules[name] = mod
    return mod, state


class RegistryShapeTests(unittest.TestCase):
    """A second integration must be one new entry, not new branching logic —
    which only holds if every entry already carries the fields the driver
    reads."""

    def test_every_entry_has_the_required_fields(self):
        self.assertTrue(integrations.INTEGRATIONS)
        for entry in integrations.INTEGRATIONS:
            for field in ("id", "label", "module", "help"):
                self.assertIn(field, entry, f"{entry.get('id')} missing {field!r}")

    def test_granola_entry_declares_its_schedule(self):
        granola = next(e for e in integrations.INTEGRATIONS if e["id"] == "granola")
        sched = granola["schedule"]
        for field in ("plist_label", "plist_template", "bin", "log_name", "description"):
            self.assertIn(field, sched)
        self.assertEqual(sched["plist_label"], "com.gigabite.granola-pull")


class NoTtyTests(unittest.TestCase):
    """A non-interactive stdin (e.g. install.sh piped, or cron) must never
    block on `input()` — it should note and return immediately."""

    def test_skips_without_prompting_when_stdin_is_not_a_tty(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=False), \
             mock.patch("builtins.input", side_effect=AssertionError("must not prompt")):
            with redirect_stdout(buf):
                integrations.run_interactive(Path("/nonexistent"))
        self.assertIn("gigabite integrations", buf.getvalue())

    def test_assume_yes_bypasses_the_tty_check(self):
        # assume_yes=True with an empty registry still has to reach the loop
        # (and thus never hit the early-return note) without a real terminal.
        buf = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=False), \
             mock.patch.object(integrations, "INTEGRATIONS", []):
            with redirect_stdout(buf):
                integrations.run_interactive(Path("/nonexistent"), assume_yes=True)
        self.assertNotIn("not an interactive terminal", buf.getvalue())


class AlreadyConnectedTests(unittest.TestCase):
    """An integration that already has a stored key must be reported as such
    and left alone by default — never silently re-prompted."""

    def setUp(self):
        self.mod_name = "tests._fake_granola_connected"
        self.mod, self.state = _fake_integration_module(self.mod_name, token="existing-key")
        self.entry = {"id": "fake", "label": "Fake — a thing", "module": self.mod_name,
                      "help": "Get it from the fake settings screen."}
        self._orig = integrations.INTEGRATIONS
        integrations.INTEGRATIONS = [self.entry]

    def tearDown(self):
        integrations.INTEGRATIONS = self._orig
        sys.modules.pop(self.mod_name, None)

    def test_default_answer_leaves_the_existing_key(self):
        buf = io.StringIO()
        # Empty answer -> the default, which the design requires to be "leave".
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value=""):
            with redirect_stdout(buf):
                integrations.run_interactive(Path("/nonexistent"))
        self.mod.store_token_interactive.assert_not_called()
        self.assertEqual(self.state["token"], "existing-key")
        self.assertIn("already connected", buf.getvalue())

    def test_answering_yes_replaces_it(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=["y", ""]):
            with redirect_stdout(buf):
                integrations.run_interactive(Path("/nonexistent"))
        self.mod.store_token_interactive.assert_called_once()
        self.assertEqual(self.state["token"], "new-token")


class EnableFlowTests(unittest.TestCase):
    """A not-yet-connected integration: enabling it stores the key; declining
    the schedule prompt must not touch launchd at all."""

    def setUp(self):
        self.mod_name = "tests._fake_granola_fresh"
        self.mod, self.state = _fake_integration_module(self.mod_name, token=None)
        self.entry = {"id": "fake", "label": "Fake — a thing", "module": self.mod_name,
                      "help": "Get it from the fake settings screen."}
        self._orig = integrations.INTEGRATIONS
        integrations.INTEGRATIONS = [self.entry]

    def tearDown(self):
        integrations.INTEGRATIONS = self._orig
        sys.modules.pop(self.mod_name, None)

    def test_declining_the_enable_prompt_never_stores_anything(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value=""):  # default is [y/N] -> no
            with redirect_stdout(buf):
                integrations.run_interactive(Path("/nonexistent"))
        self.mod.store_token_interactive.assert_not_called()

    def test_enabling_without_a_schedule_field_never_asks_to_schedule(self):
        self.entry.pop("schedule", None)  # this entry never had one — belt and braces
        buf = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=["y", ""]):
            with redirect_stdout(buf):
                integrations.run_interactive(Path("/nonexistent"))
        self.mod.store_token_interactive.assert_called_once()
        self.assertNotIn("Schedule the daily pull", buf.getvalue())


class ScheduleTests(unittest.TestCase):
    """Scheduling renders the plist template and drives launchd — with
    `subprocess.run` and `Path.home` faked out so nothing real is touched."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gigabite-integrations-test-"))
        self.repo_root = self.tmp / "repo"
        self.fake_home = self.tmp / "home"
        (self.repo_root / "install" / "launchd").mkdir(parents=True)
        (self.repo_root / "bin").mkdir(parents=True)
        self.fake_home.mkdir(parents=True)

        template = self.repo_root / "install" / "launchd" / "com.gigabite.fake-pull.plist"
        template.write_text(
            "<plist><dict><key>ProgramArguments</key><array><string>__DAILY_BIN__"
            "</string></array><key>StandardOutPath</key><string>__LOG__</string>"
            "</dict></plist>",
            encoding="utf-8",
        )
        (self.repo_root / "bin" / "gigabite-fake-pull").write_text("#!/bin/sh\n", encoding="utf-8")

        self.mod_name = "tests._fake_granola_schedule"
        self.mod, self.state = _fake_integration_module(self.mod_name, token=None)
        self.entry = {
            "id": "fake", "label": "Fake — a thing", "module": self.mod_name,
            "help": "Get it from the fake settings screen.",
            "schedule": {
                "plist_label": "com.gigabite.fake-pull",
                "plist_template": "install/launchd/com.gigabite.fake-pull.plist",
                "bin": "bin/gigabite-fake-pull",
                "log_name": "gigabite-fake-pull.log",
                "description": "daily fake pull at 00:00",
            },
        }
        self._orig_registry = integrations.INTEGRATIONS
        integrations.INTEGRATIONS = [self.entry]

    def tearDown(self):
        integrations.INTEGRATIONS = self._orig_registry
        sys.modules.pop(self.mod_name, None)

    def test_accepting_the_schedule_prompt_writes_and_loads_the_plist(self):
        fake_run = mock.Mock(return_value=types.SimpleNamespace(returncode=0))
        buf = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=["y", "", "y"]), \
             mock.patch("pathlib.Path.home", return_value=self.fake_home), \
             mock.patch.object(integrations.subprocess, "run", fake_run):
            with redirect_stdout(buf):
                integrations.run_interactive(self.repo_root)

        self.mod.store_token_interactive.assert_called_once()
        plist_path = self.fake_home / "Library" / "LaunchAgents" / "com.gigabite.fake-pull.plist"
        self.assertTrue(plist_path.exists())
        rendered = plist_path.read_text(encoding="utf-8")
        self.assertIn(str(self.repo_root / "bin" / "gigabite-fake-pull"), rendered)
        self.assertIn(str(self.fake_home / "Library" / "Logs" / "gigabite-fake-pull.log"), rendered)
        self.assertNotIn("__DAILY_BIN__", rendered)
        self.assertNotIn("__LOG__", rendered)

        # bootout (ignored failure) then bootstrap — never a real launchctl.
        self.assertEqual(fake_run.call_count, 2)
        self.assertIn("bootout", fake_run.call_args_list[0].args[0])
        self.assertIn("bootstrap", fake_run.call_args_list[1].args[0])
        self.assertIn("scheduled: daily fake pull at 00:00", buf.getvalue())

    def test_declining_the_schedule_prompt_never_calls_launchctl(self):
        fake_run = mock.Mock(return_value=types.SimpleNamespace(returncode=0))
        buf = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=["y", "", "n"]), \
             mock.patch("pathlib.Path.home", return_value=self.fake_home), \
             mock.patch.object(integrations.subprocess, "run", fake_run):
            with redirect_stdout(buf):
                integrations.run_interactive(self.repo_root)
        fake_run.assert_not_called()

    def test_a_failed_bootstrap_reports_the_fallback_line(self):
        fake_run = mock.Mock(return_value=types.SimpleNamespace(returncode=1))
        buf = io.StringIO()
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=["y", "", "y"]), \
             mock.patch("pathlib.Path.home", return_value=self.fake_home), \
             mock.patch.object(integrations.subprocess, "run", fake_run):
            with redirect_stdout(buf):
                integrations.run_interactive(self.repo_root)
        self.assertIn("couldn't load it now", buf.getvalue())


class GranolaLoginRefactorTests(unittest.TestCase):
    """`cmd_granola_login` now calls into `integrations.run_secure_prompt`,
    but its output for a direct caller must stay exactly what it was before
    the refactor — in particular, cancelling must NOT print the "not saved"
    line that only belongs to a prompt that actually ran and failed."""

    def test_cancelling_the_secure_prompt_prints_only_the_cancelled_line(self):
        from gigabite import cli
        buf = io.StringIO()
        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            with redirect_stdout(buf):
                rc = cli.cmd_granola_login(None)
        self.assertEqual(rc, 1)
        out = buf.getvalue()
        self.assertIn("cancelled.", out)
        self.assertNotIn("was not saved", out)


if __name__ == "__main__":
    unittest.main()
