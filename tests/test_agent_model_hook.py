"""Tests for `install/hooks/gg-agent-model.sh`, the PreToolUse hook that makes every
agent spawn name its model (core.md §6). The properties pinned here:

  A. an Agent call with no `model` is blocked (exit 2) and told how to choose;
  B. one that names a model proceeds, whichever model it names;
  C. a fork proceeds without one — it copies the parent's model and ignores `model`;
  D. the older tool name, Task, is held to the same rule;
  E. anything the hook can't read proceeds: another tool, bad JSON, no payload.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "install" / "hooks" / "gg-agent-model.sh"


def payload(tool_input, tool_name="Agent"):
    return json.dumps({"session_id": "abc-123", "hook_event_name": "PreToolUse",
                       "tool_name": tool_name, "tool_input": tool_input, "cwd": "/"})


@unittest.skipUnless(Path("/bin/bash").exists() and Path("/usr/bin/python3").exists(),
                     "the hook is bash + /usr/bin/python3 by construction")
class AgentModelHook(unittest.TestCase):

    def fire(self, stdin: str):
        return subprocess.run(["/bin/bash", str(HOOK)], input=stdin, text=True,
                              capture_output=True, timeout=30,
                              env={"PATH": "/usr/bin:/bin"})

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


if __name__ == "__main__":
    unittest.main()
