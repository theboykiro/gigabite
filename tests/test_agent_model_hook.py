"""Tests for `gigabite hook agent-model` (gigabite/hooks.py), the PreToolUse hook that
makes every agent spawn name its model (core.md §6), and for the macOS wrapper an
install registers, `install/hooks/gg-agent-model.sh`. The properties pinned here:

  A. an Agent call with no `model` is blocked (exit 2) and told how to choose;
  B. one that names a model proceeds, whichever model it names;
  C. a fork proceeds without one — it copies the parent's model and ignores `model`;
  D. the older tool name, Task, is held to the same rule;
  E. anything the hook can't read proceeds: another tool, bad JSON, no payload;
  F. the wrapper passes the verdict through, and proceeds when the launcher is gone.

    python3 -m unittest discover -s tests        (from the repo root)
"""

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
HOOK_SOURCE = REPO / "install" / "hooks" / "gg-agent-model.sh"


def payload(tool_input, tool_name="Agent"):
    return json.dumps({"session_id": "abc-123", "hook_event_name": "PreToolUse",
                       "tool_name": tool_name, "tool_input": tool_input, "cwd": "/"})


class AgentModelHook(unittest.TestCase):
    """The hook itself, as Claude Code runs it on Windows."""

    def fire(self, stdin: str):
        return subprocess.run([sys.executable, "-m", "gigabite", "hook", "agent-model"],
                              input=stdin, text=True, encoding="utf-8",
                              capture_output=True, timeout=30,
                              env=dict(os.environ, PYTHONPATH=str(REPO)))

    def test_an_agent_with_no_model_is_blocked_and_told_how_to_choose(self):
        proc = self.fire(payload({"description": "scan", "prompt": "find x",
                                  "subagent_type": "general-purpose"}))
        self.assertEqual(2, proc.returncode)
        self.assertEqual("", proc.stdout)
        self.assertIn("names no model", proc.stderr)
        self.assertIn('"sonnet"', proc.stderr)
        self.assertIn('"opus"', proc.stderr)

    def test_a_blank_model_counts_as_none(self):
        self.assertEqual(2, self.fire(payload({"prompt": "x", "model": "  "})).returncode)

    def test_an_agent_that_names_a_model_proceeds(self):
        for model in ("sonnet", "opus", "haiku"):
            proc = self.fire(payload({"prompt": "x", "model": model}))
            self.assertEqual(0, proc.returncode, model)
            self.assertEqual("", proc.stderr, model)

    def test_a_fork_proceeds_without_a_model(self):
        proc = self.fire(payload({"prompt": "x", "subagent_type": "fork"}))
        self.assertEqual(0, proc.returncode)

    def test_the_older_task_name_is_held_to_the_same_rule(self):
        self.assertEqual(2, self.fire(payload({"prompt": "x"}, "Task")).returncode)
        self.assertEqual(0, self.fire(payload({"prompt": "x", "model": "opus"},
                                              "Task")).returncode)

    def test_what_it_cannot_read_proceeds(self):
        for stdin in (payload({"command": "ls"}, "Bash"), "not json", "", "[]",
                      json.dumps({"tool_name": "Agent", "tool_input": "x"})):
            self.assertEqual(0, self.fire(stdin).returncode, stdin)


@unittest.skipUnless(Path("/bin/bash").exists(), "the wrapper is bash by construction")
class AgentModelWrapper(AgentModelHook):
    """The same properties through `gg-agent-model.sh`, as a macOS install runs it."""

    def setUp(self):
        super().setUp()
        self.base = Path(tempfile.mkdtemp(prefix="gigabite-agent-model-"))
        self.addCleanup(shutil.rmtree, str(self.base), True)
        launcher = self.base / "gigabite"
        launcher.write_text('#!/bin/sh\nexec "%s" -m gigabite "$@"\n' % sys.executable,
                            encoding="utf-8")
        launcher.chmod(0o755)
        self.hook = self.install_hook(launcher)

    def install_hook(self, launcher: Path) -> Path:
        """What install.sh does with it: the launcher's absolute path baked in."""
        hook = self.base / "gg-agent-model.sh"
        hook.write_text(HOOK_SOURCE.read_text(encoding="utf-8")
                        .replace("__GIGABITE_BIN__", str(launcher)), encoding="utf-8")
        return hook

    def fire(self, stdin: str, hook=None):
        return subprocess.run(["/bin/bash", str(hook or self.hook)], input=stdin, text=True,
                              capture_output=True, timeout=30,
                              env=dict(os.environ, PYTHONPATH=str(REPO)))

    def test_a_launcher_that_is_gone_lets_the_call_through(self):
        hook = self.install_hook(self.base / "moved-away" / "gigabite")
        proc = self.fire(payload({"prompt": "x"}), hook=hook)
        self.assertEqual((0, ""), (proc.returncode, proc.stderr))


if __name__ == "__main__":
    unittest.main()
