"""Tests for `install/hooks/gg-refresh.sh`, the SessionStart hook that keeps the
index fresh. It replaced the 18:00 launchd job, so the properties pinned here are
the ones that job could not offer and the ones a hook must not break:

  A. it returns at once and prints nothing — SessionStart stdout is added to the
     session's context, and a hook that waited on the ingest would delay every
     session by however long the ingest takes;
  B. the ingest still runs, detached, and says how it went in the log;
  C. one refresh at a time — two sessions starting together don't ingest twice in
     parallel — and a finished refresh releases the lock for the next session;
  D. a launcher that has gone away (repo moved or deleted) is a silent no-op;
  E. for real: a session written after the index was built is recallable once
     the hook has run.

Every run gets its own HOME and knowledge root; nothing here reads the real ones.

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


@unittest.skipUnless(Path("/bin/bash").exists() and Path("/usr/bin/python3").exists(),
                     "the hook is bash + /usr/bin/python3 by construction")
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
        """What install.sh does with it: the launcher's absolute path baked in."""
        hook = self.base / "gg-refresh.sh"
        hook.write_text(HOOK_SOURCE.read_text(encoding="utf-8")
                        .replace("__GIGABITE_BIN__", str(launcher)), encoding="utf-8")
        hook.chmod(0o755)
        return hook

    def fake_launcher(self, seconds: float) -> Path:
        """Records each call, takes `seconds` about it, succeeds."""
        fake = self.base / "fake-gigabite"
        fake.write_text('#!/bin/sh\necho "$@" >> "%s"\nsleep %s\necho indexed\n'
                        % (self.calls, seconds), encoding="utf-8")
        fake.chmod(0o755)
        return fake

    def fire(self, hook: Path):
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin",
               "PYTHONDONTWRITEBYTECODE": "1",
               "GIGABITE_KNOWLEDGE_DIR": str(self.knowledge),
               "GIGABITE_CORE_DIR": str(self.home / ".core")}
        start = time.monotonic()
        proc = subprocess.run(["/bin/bash", str(hook)], input=PAYLOAD, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=30)
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
        hook = self.install_hook(self.fake_launcher(3))
        proc, took = self.fire(hook)
        self.assertEqual(0, proc.returncode)
        self.assertEqual("", proc.stdout, "SessionStart stdout lands in the context")
        self.assertEqual("", proc.stderr)
        self.assertLess(took, 1.0, "the hook waited for the ingest")
        self.assertTrue(self.wait_for(lambda: self.calls_made() == ["ingest"]),
                        "the ingest never started")
        self.assertNotIn("refresh done", self.log(), "it can't have finished yet")
        self.assertTrue(self.wait_for(lambda: "refresh done (exit 0)" in self.log()),
                        self.log())
        self.assertIn("indexed", self.log(), "the ingest's own output is kept")

    def test_a_launcher_that_is_gone_is_a_silent_no_op(self):
        hook = self.install_hook(self.base / "moved-away" / "gigabite")
        proc, _took = self.fire(hook)
        self.assertEqual(0, proc.returncode)
        self.assertEqual("", proc.stdout + proc.stderr)
        self.assertFalse(self.data.exists(), "created a data dir for nothing")


class TestOneRefreshAtATime(RefreshCase):

    def test_a_second_session_starting_mid_refresh_does_not_ingest_again(self):
        hook = self.install_hook(self.fake_launcher(2))
        self.fire(hook)
        self.assertTrue(self.wait_for(lambda: self.calls_made() == ["ingest"]))
        self.fire(hook)
        self.assertTrue(self.wait_for(lambda: "refresh done" in self.log()), self.log())
        time.sleep(0.3)
        self.assertEqual(["ingest"], self.calls_made())

    def test_a_finished_refresh_releases_the_lock(self):
        hook = self.install_hook(self.fake_launcher(0))
        self.fire(hook)
        self.assertTrue(self.wait_for(lambda: self.log().count("refresh done") == 1))
        self.fire(hook)
        self.assertTrue(self.wait_for(lambda: self.log().count("refresh done") == 2),
                        self.log())
        self.assertEqual(["ingest", "ingest"], self.calls_made())


class TestItReallyRefreshesTheIndex(RefreshCase):
    """The real launcher, a real index, and a session written after it was built."""

    def gigabite(self, *args):
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin",
               "PYTHONDONTWRITEBYTECODE": "1",
               "GIGABITE_KNOWLEDGE_DIR": str(self.knowledge),
               "GIGABITE_CORE_DIR": str(self.home / ".core")}
        return subprocess.run([str(REPO / "bin" / "gigabite")] + list(args), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=120)

    def test_a_new_session_is_searchable_after_the_hook_runs(self):
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
        (folder / "s1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        self.assertNotIn("quokka", self.gigabite("search", "quokka").stdout.lower()
                         .replace("no matches for 'quokka'", ""))

        proc, took = self.fire(self.install_hook(REPO / "bin" / "gigabite"))
        self.assertEqual(("", 0), (proc.stdout, proc.returncode))
        self.assertTrue(self.wait_for(lambda: "refresh done" in self.log(), timeout=90),
                        self.log())
        self.assertIn("refresh done (exit 0)", self.log())
        self.assertIn("expire the quokka cache hourly",
                      self.gigabite("search", "quokka").stdout.replace("«", "").replace("»", ""))


if __name__ == "__main__":
    unittest.main()
