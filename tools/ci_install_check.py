#!/usr/bin/env python3
"""CI: are the hooks an install registered ones Claude Code can actually run?

    python tools/ci_install_check.py installed     # after `gigabite install`
    python tools/ci_install_check.py uninstalled   # after `gigabite uninstall --yes`

Reads ~/.claude/settings.json (USERPROFILE on Windows) and, for `installed`,
requires each of the three hooks exactly once, then runs every one the way Claude
Code does: the registered command with its args, no shell, the payload on stdin.
On Windows that is the exec form (`command` + `args`); on macOS, the .sh wrapper.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOKS = (("UserPromptSubmit", None, "recall"), ("SessionStart", None, "refresh"),
         ("PreToolUse", "Agent|Task", "agent-model"))


def fail(msg: str) -> None:
    print("FAIL: " + msg)
    sys.exit(1)


def ours(settings: Path) -> list:
    """(event, matcher, hook) for every gigabite hook registered."""
    if not settings.exists():
        return []
    cfg = json.loads(settings.read_text(encoding="utf-8-sig"))
    out = []
    for event, entries in (cfg.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                text = json.dumps(hook)
                if "gg-" in text or hook.get("args", [None])[:1] == ["hook"]:
                    out.append((event, entry.get("matcher"), hook))
    return out


def run(hook: dict, payload: dict) -> subprocess.CompletedProcess:
    argv = [hook["command"]] + list(hook.get("args", []))
    return subprocess.run(argv, input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", timeout=120)


def main() -> None:
    settings = Path.home() / ".claude" / "settings.json"
    found = ours(settings)
    if sys.argv[1:] == ["uninstalled"]:
        if found:
            fail("still registered after uninstall: %r" % found)
        print("ok: no gigabite hooks in %s" % settings)
        return
    if sys.argv[1:] != ["installed"]:
        fail("usage: ci_install_check.py installed|uninstalled")

    by_name = {}
    for event, matcher, hook in found:
        name = hook["args"][1] if hook.get("args") else hook["command"].rsplit("gg-", 1)[-1][:-3]
        by_name.setdefault(name, []).append((event, matcher, hook))
    for event, matcher, name in HOOKS:
        entries = by_name.get(name, [])
        if len(entries) != 1:
            fail("%s registered %d times: %r" % (name, len(entries), entries))
        got_event, got_matcher, hook = entries[0]
        if (got_event, got_matcher) != (event, matcher):
            fail("%s on %s/%s, wanted %s/%s" % (name, got_event, got_matcher, event, matcher))
        command = hook["command"]
        if sys.platform == "win32":
            if hook.get("args") != ["hook", name] or "\\" in command \
                    or not command.lower().endswith(".exe"):
                fail("%s is not exec form with a forward-slash .exe: %r" % (name, hook))
        if not Path(command).is_file():
            fail("%s points at %s, which does not exist" % (name, command))
        print("ok: %s on %s -> %r" % (name, event, hook))

    hook = lambda name: by_name[name][0][2]                      # noqa: E731
    spawn = {"tool_name": "Agent", "tool_input": {"prompt": "x"}}
    proc = run(hook("agent-model"), spawn)
    if proc.returncode != 2 or "names no model" not in proc.stderr:
        fail("agent-model let a spawn with no model through: %r" % proc)
    spawn["tool_input"]["model"] = "sonnet"
    proc = run(hook("agent-model"), spawn)
    if proc.returncode != 0:
        fail("agent-model blocked a spawn that names its model: %r" % proc)
    for name, payload in (("recall", {"prompt": "what did we decide", "session_id": "ci"}),
                          ("refresh", {"session_id": "ci"})):
        proc = run(hook(name), payload)
        if proc.returncode != 0:
            fail("%s exited %s: %r" % (name, proc.returncode, proc))
    print("ok: all three hooks run as Claude Code runs them")


if __name__ == "__main__":
    main()
