"""Tests for `gigabite hook refresh` (gigabite/hooks.py), the SessionStart hook that
keeps the index fresh, and for its macOS wrapper `install/hooks/gg-refresh.sh`. It replaced the 18:00 launchd job, so the properties pinned here are
the ones that job could not offer and the ones a hook must not break:

  A. it returns at once and prints nothing — SessionStart stdout is added to the
     session's context, and a hook that waited on the ingest would delay every
     session by however long the ingest takes;
  B. the ingest still runs, detached, and says how it went in the log;
  C. one refresh at a time — two sessions starting together don't ingest twice in
     parallel — and a finished refresh releases the lock for the next session;
  D. a launcher that has gone away (repo moved or deleted) is a silent no-op;
  E. for real: a session written after the index was built is recallable once
     the hook has run;
  F. the one exception to A: the first session after a first install prints a
     one-time notice of what the install made, once, and never again.

Every run gets its own home and knowledge root; nothing here reads the real ones.
The hook is driven the way Claude Code drives it on Windows, through Python, with a
fake ingest where the test needs one to take a while; D, and one end-to-end run,
go through the wrapper as a macOS install registers it.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

REPO = Path(__file__).resolve().parent.parent
HOOK_SOURCE = REPO / "install" / "hooks" / "gg-refresh.sh"
PAYLOAD = json.dumps({"session_id": "abc-123", "hook_event_name": "SessionStart",
                      "source": "startup", "cwd": "/"})

# `gigabite hook refresh`, with the command `ingest` is appended to taken from argv,
# so a test can hand it a fake. With no argv it is the real one.
DRIVER = """
import sys
from gigabite import hooks
sys.stdin.read()
out = hooks.refresh(launcher=sys.argv[1:] or None)
if out:
    print(out)
"""
FAKE_INGEST = """
import os, sys, time
with open(sys.argv[1], "a") as fh:
    fh.write(" ".join(sys.argv[3:]) + "\\n")
seconds = sys.argv[2]
if os.path.isabs(seconds):                 # a file to wait for, not a duration
    deadline = time.monotonic() + 60
    while not os.path.exists(seconds) and time.monotonic() < deadline:
        time.sleep(0.05)
else:
    time.sleep(float(seconds))
print("indexed")
"""
BASH = Path("/bin/bash").exists()


class RefreshCase(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.base = Path(tempfile.mkdtemp(prefix="gigabite-refresh-"))
        self.addCleanup(shutil.rmtree, str(self.base), True)
        self.home = self.base / "home"
        self.knowledge = self.home / "Knowledge"
        self.data = self.knowledge / ".gigabite"
        self.home.mkdir(parents=True)
        self.calls = self.base / "calls.log"

    def install_hook(self, launcher: Path) -> Path:
        """What install.sh does with the wrapper: the launcher's absolute path baked in."""
        hook = self.base / "gg-refresh.sh"
        hook.write_text(HOOK_SOURCE.read_text(encoding="utf-8")
                        .replace("__GIGABITE_BIN__", str(launcher)), encoding="utf-8")
        hook.chmod(0o755)
        return hook

    def fake_ingest(self, seconds) -> list:
        """A launcher that records each call, takes `seconds` about it, succeeds.
        Given a path instead, it takes until that file exists."""
        fake = self.base / "fake_ingest.py"
        fake.write_text(FAKE_INGEST, encoding="utf-8")
        return [sys.executable, str(fake), str(self.calls), str(seconds)]

    def env(self) -> dict:
        return dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                    PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1",
                    GIGABITE_KNOWLEDGE_DIR=str(self.knowledge),
                    GIGABITE_CORE_DIR=str(self.home / ".core"))

    def fire(self, launcher=(), hook: Path = None):
        """The hook, through Python with *launcher* standing in for gigabite, or
        through the bash wrapper at *hook*."""
        argv = (["/bin/bash", str(hook)] if hook
                else [sys.executable, "-c", DRIVER] + list(launcher))
        start = time.monotonic()
        proc = subprocess.run(argv, input=PAYLOAD, env=self.env(), encoding="utf-8",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return proc, time.monotonic() - start

    def wait_for(self, predicate, timeout=30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def log(self) -> str:
        path = self.data / "refresh.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def calls_made(self) -> list:
        return self.calls.read_text().splitlines() if self.calls.exists() else []


class TestItNeverHoldsUpTheSession(RefreshCase):

    def test_it_returns_at_once_silently_while_the_ingest_runs_on(self):
        proc, took = self.fire(self.fake_ingest(4))
        self.assertEqual(0, proc.returncode)
        self.assertEqual("", proc.stdout, "SessionStart stdout lands in the context")
        self.assertEqual("", proc.stderr)
        # Generous for a cold Python on a CI runner; the fake ingest takes twice this.
        self.assertLess(took, 2.0, "the hook waited for the ingest")
        self.assertTrue(self.wait_for(lambda: self.calls_made() == ["ingest"]),
                        "the ingest never started")
        self.assertNotIn("refresh done", self.log(), "it can't have finished yet")
        self.assertTrue(self.wait_for(lambda: "refresh done (exit 0)" in self.log()),
                        self.log())
        self.assertIn("indexed", self.log(), "the ingest's own output is kept")

    @unittest.skipUnless(BASH, "the wrapper is bash by construction")
    def test_a_launcher_that_is_gone_is_a_silent_no_op(self):
        proc, _took = self.fire(hook=self.install_hook(self.base / "moved-away" / "gigabite"))
        self.assertEqual(0, proc.returncode)
        self.assertEqual("", proc.stdout + proc.stderr)
        self.assertFalse(self.data.exists(), "created a data dir for nothing")


class TestOneRefreshAtATime(RefreshCase):

    def test_a_second_session_starting_mid_refresh_does_not_ingest_again(self):
        # The first ingest runs until the test lets it go, not for a fixed time a
        # slow runner's two cold Python starts could outlast.
        release = self.base / "release"
        held = self.fake_ingest(release)
        self.fire(held)
        self.assertTrue(self.wait_for(lambda: self.calls_made() == ["ingest"]))
        self.fire(held)
        time.sleep(2.0)                     # the second worker's chance to ingest
        release.touch()
        self.assertTrue(self.wait_for(lambda: "refresh done" in self.log()), self.log())
        time.sleep(0.5)
        self.assertEqual(["ingest"], self.calls_made())

    def test_a_finished_refresh_releases_the_lock(self):
        quick = self.fake_ingest(0)
        self.fire(quick)
        self.assertTrue(self.wait_for(lambda: self.log().count("refresh done") == 1))
        self.fire(quick)
        self.assertTrue(self.wait_for(lambda: self.log().count("refresh done") == 2),
                        self.log())
        self.assertEqual(["ingest", "ingest"], self.calls_made())


class TestTheFirstSessionNotice(RefreshCase):

    def pend(self):
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "welcome-pending").touch()

    def notice(self, proc) -> dict:
        self.assertEqual(0, proc.returncode)
        self.assertEqual("", proc.stderr)
        return json.loads(proc.stdout)

    def test_it_is_shown_once_to_the_user_and_to_claude(self):
        self.pend()
        quick = self.fake_ingest(0)
        proc, took = self.fire(quick)
        self.assertLess(took, 2.0)
        out = self.notice(proc)
        self.assertIn(os.path.join("~", "Knowledge"), out["systemMessage"])
        self.assertIn(os.path.join("~", "Knowledge", "README.md"), out["systemMessage"])
        spec = out["hookSpecificOutput"]
        self.assertEqual("SessionStart", spec["hookEventName"])
        self.assertIn(out["systemMessage"], spec["additionalContext"])
        self.assertFalse((self.data / "welcome-pending").exists())
        self.assertTrue(self.wait_for(lambda: "refresh done" in self.log()),
                        "the notice replaced the refresh instead of preceding it")

        proc, _took = self.fire(quick)
        self.assertEqual("", proc.stdout, "shown a second time")

    def test_an_unfinished_protocol_is_pointed_at_core_setup(self):
        self.pend()
        core = self.home / ".core" / "core.md"
        core.parent.mkdir(parents=True)
        core.write_text("## Voice\n**[FILL]**\n", encoding="utf-8")
        proc, _took = self.fire(self.fake_ingest(0))
        self.assertIn("/core-setup", self.notice(proc)["systemMessage"])

    def test_a_finished_protocol_is_not_mentioned(self):
        self.pend()
        core = self.home / ".core" / "core.md"
        core.parent.mkdir(parents=True)
        core.write_text("## Voice\nterse\n", encoding="utf-8")
        proc, _took = self.fire(self.fake_ingest(0))
        self.assertNotIn("/core-setup", self.notice(proc)["systemMessage"])

    @unittest.skipUnless(BASH, "the wrapper is bash by construction")
    def test_a_launcher_that_is_gone_announces_nothing(self):
        self.pend()
        proc, _took = self.fire(hook=self.install_hook(self.base / "moved-away" / "gigabite"))
        self.assertEqual("", proc.stdout)
        self.assertTrue((self.data / "welcome-pending").exists(),
                        "consumed the notice without showing it")


class TestItReallyRefreshesTheIndex(RefreshCase):
    """The real launcher, a real index, and a session written after it was built."""

    def gigabite(self, *args):
        return subprocess.run([sys.executable, "-m", "gigabite"] + list(args), env=self.env(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", timeout=120)

    def test_a_new_session_is_searchable_after_the_hook_runs(self):
        self.assert_the_hook_refreshes(self.fire)

    @unittest.skipUnless(BASH and (REPO / "bin" / "gigabite").exists(),
                         "the wrapper and its launcher are bash by construction")
    def test_through_the_macos_wrapper_too(self):
        hook = self.install_hook(REPO / "bin" / "gigabite")
        self.assert_the_hook_refreshes(lambda: self.fire(hook=hook))

    def assert_the_hook_refreshes(self, fire):
        self.assertEqual(0, self.gigabite("ingest").returncode)
        folder = self.home / ".claude" / "projects" / "-code-widgetshop"
        folder.mkdir(parents=True)
        events = [
            {"type": "user", "sessionId": "s1", "cwd": "/code/widgetshop",
             "timestamp": "2026-09-01T10:00:00Z",
             "message": {"role": "user", "content": "how should the quokka cache expire"}},
            {"type": "assistant", "sessionId": "s1", "cwd": "/code/widgetshop",
             "timestamp": "2026-09-01T10:00:05Z",
             "message": {"role": "assistant", "content": [
                 {"type": "text", "text": "expire the quokka cache hourly"}]}},
        ]
        (folder / "s1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events),
                                         encoding="utf-8")
        self.assertNotIn("quokka", self.gigabite("search", "quokka").stdout.lower()
                         .replace("no matches for 'quokka'", ""))

        proc, _took = fire()
        self.assertEqual(("", 0), (proc.stdout, proc.returncode))
        self.assertTrue(self.wait_for(lambda: "refresh done" in self.log(), timeout=90),
                        self.log())
        self.assertIn("refresh done (exit 0)", self.log())
        self.assertIn("expire the quokka cache hourly",
                      self.gigabite("search", "quokka").stdout.replace("«", "").replace("»", ""))


if __name__ == "__main__":
    unittest.main()
