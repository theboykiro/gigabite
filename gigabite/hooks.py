"""The Claude Code hooks, in Python, so one implementation runs on macOS and Windows.

    gigabite hook recall        UserPromptSubmit: ambient recall
    gigabite hook refresh       SessionStart: keeps the index fresh
    gigabite hook agent-model   PreToolUse, matcher Agent|Task: every spawn names its model

Each reads the hook payload on stdin. The contract with Claude Code, for all three:
never fail the turn. Anything unreadable or unexpected exits 0 and prints nothing.
The one deliberate non-zero exit is agent-model's 2, which blocks the call.

On macOS, `install/hooks/*.sh` are thin wrappers that hand the payload to these,
so an existing install keeps the hook paths it registered.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from . import config, osutil

NAMES = ("recall", "refresh", "agent-model")


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------

def recall(payload: str) -> str:
    """Strongly-relevant prior context for the prompt in *payload*, or "".

    Silent unless a hit genuinely matches the prompt, so it never derails other
    work. What is printed is added to the turn's context.
    """
    try:
        event = json.loads(payload)
    except ValueError:
        return ""
    if not isinstance(event, dict):
        return ""
    prompt = event.get("prompt")
    # An empty prompt has nothing to route. Everything else goes to the router: the
    # `register` axis owns the "is this turn worth recalling for?" call, so a
    # word-count pre-filter here could only disagree with it, and it did, silently,
    # in the direction that loses recall: `widget pricing` is two words and a real
    # project's keywords, which is precisely the short turn that should recall.
    if not isinstance(prompt, str) or not prompt:
        return ""
    # The session id lets an unanswered "which project?" come back once per session
    # (features/bindings.py). Safe characters only.
    session = event.get("session_id")
    session = re.sub(r"[^A-Za-z0-9_-]", "", session if isinstance(session, str) else "")[:128]

    from .features import bindings, routing
    from .store import Store, connect
    d = routing.route(Store(connect()), prompt, limit=6, session_id=session or None)
    ask = d.get("ask") or None
    if ask:
        # Recorded by the caller that shows the question, as in `cmd_route`.
        bindings.record_ask(ask["dir"], session_id=session or None)

    # The register router (features/routing.py): a sparring turn gets nothing injected.
    if (d.get("register") or {}).get("mode") == routing.SPAR:
        return ""
    # The router decides which hits are worth injecting (`inject`, see
    # features/routing.py: coverage of the prompt's meaningful words, plus a relative
    # score check when the project came from a keyword).
    strong = [h for h in (d.get("hits") or []) if isinstance(h, dict) and h.get("inject") is True]
    if not strong:
        # Nothing recalled. When the turn resolved to no project at all, the router
        # hands back a one-time question instead: asking which project this folder is
        # beats vanishing, because silence reads as "no memory of this" and a fresh
        # install has no projects to resolve to.
        text = ask.get("text") if isinstance(ask, dict) else None
        return text if isinstance(text, str) else ""
    ctx = d.get("context") or {}
    out = ["[gigabite recall — prior context from your own history; cite as source · title · date if used]"]
    if ctx.get("project"):
        out.append(f"detected context: {ctx['project']} ({ctx.get('confidence')})")
    seen = set()
    for h in strong:
        if h["doc_id"] in seen:
            continue
        seen.add(h["doc_id"])
        date = (h.get("created_utc") or "")[:10]
        snip = " ".join((h.get("snippet") or "").split())[:240]
        out.append(f"- {h.get('title', '?')} [{h.get('source')} · {date} · {h['doc_id']}]: {snip}")
        if len(seen) >= 5:
            break
    return "\n".join(out)


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------

# A refresh still running after this long is stopped.
REFRESH_TIMEOUT = 900
# refresh.log is trimmed to its last LOG_KEEP bytes once it passes LOG_MAX.
LOG_MAX, LOG_KEEP = 262144, 131072


def refresh(launcher: Optional[list] = None) -> str:
    """Start an incremental `gigabite ingest`, detached, and return at once.

    So the work from earlier today is recallable in this session: the index is
    refreshed when it is about to be used, not at a fixed hour when the machine
    may be asleep. Returns what to print, which is nothing (SessionStart stdout is
    added to the session's context) except once: the first session after a first
    install gets a notice of what the install made.

    *launcher* is the command `ingest` is appended to; this interpreter running
    this package unless a test says otherwise.
    """
    out = _welcome_notice()
    launcher = launcher or [sys.executable, "-m", "gigabite"]
    # The worker must import this package however this process found it: through
    # PYTHONPATH from `bin/gigabite`, or from site-packages under an installed tool.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")) if p)
    osutil.detach([sys.executable, "-m", "gigabite.hooks", "refresh-worker",
                   str(config.MACHINE_DIR), *launcher], env=env)
    return out


def _welcome_notice() -> str:
    """The first session after a first install says once what the install made.

    When Claude Code ran the installer, the user saw only its summary, and this
    session is the one step every install reaches, because a restart is required.
    The installer leaves the marker; claiming it by rename means two sessions
    starting together print it once. `systemMessage` is shown to the user;
    `additionalContext` reaches Claude, in case the client does not show the
    former and so Claude can answer questions about it.
    """
    data = config.MACHINE_DIR
    try:
        os.replace(str(data / "welcome-pending"), str(data / "welcome-shown"))
    except OSError:
        return ""
    try:
        unfilled = "[FILL]" in config.CORE_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        unfilled = False
    k = osutil.tilde(config.KNOWLEDGE_DIR)
    message = (f"gigabite is set up. It made {k}, the folder for your notes, meeting "
               f"transcripts and files, one subfolder per project. Everything in it is "
               f"searchable from Claude Code. What goes where: {os.path.join(k, 'README.md')}")
    if unfilled:
        message += (f"\nYour working rules for Claude are in {osutil.tilde(config.CORE_FILE)}. "
                    f"Run /core-setup to fill them in.")
    context = ("[gigabite: first session since install] The user may not know what the "
               "install created. At the start of your first reply, pass this on in two or "
               "three plain sentences, once:\n" + message)
    return json.dumps({"systemMessage": message,
                       "hookSpecificOutput": {"hookEventName": "SessionStart",
                                              "additionalContext": context}})


def refresh_worker(data: Path, launcher: list) -> None:
    """The detached half of `refresh`: one refresh at a time, time-boxed, logged.

    One at a time: a second session starting mid-refresh skips it. The lock is
    released by the OS if the refresh dies, so a crash cannot wedge it. Output goes
    to <knowledge>/.gigabite/refresh.log, trimmed to its last ~128 KB.
    """
    data.mkdir(parents=True, exist_ok=True)
    held = osutil.lock(data / "refresh.lock", wait=False)
    if held is None:
        return                                 # another session is already refreshing
    try:
        log_path = data / "refresh.log"
        try:
            if log_path.stat().st_size > LOG_MAX:
                with open(log_path, "rb") as fh:
                    fh.seek(-LOG_KEEP, os.SEEK_END)
                    tail = fh.read()
                with open(log_path, "wb") as fh:
                    fh.write(tail)
        except OSError:
            pass
        stamp = lambda: time.strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
        argv = list(launcher) + ["ingest"]
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"{stamp()}  refresh: {' '.join(argv)}\n")
            log.flush()
            try:
                rc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    timeout=REFRESH_TIMEOUT, **osutil.NO_WINDOW).returncode
            except subprocess.TimeoutExpired:
                rc = f"timed out after {REFRESH_TIMEOUT}s"
            log.flush()
            log.write(f"{stamp()}  refresh done (exit {rc})\n")
    finally:
        osutil.unlock(held)


# ---------------------------------------------------------------------------
# agent-model
# ---------------------------------------------------------------------------

def agent_model(payload: str) -> tuple:
    """(exit code, stderr) for a PreToolUse payload.

    core.md §6 says to pick the model tier by the cost of being wrong. As prose that
    is a request, not a mechanism: an Agent call with no `model` silently inherits
    the session's model, and nobody chose it. This refuses that call and tells
    Claude to choose, so the choice is made on every spawn; there is deliberately no
    default. A fork copies the parent's model and ignores `model`, so it is let
    through. Anything this can't read (no payload, bad JSON, another tool) proceeds:
    a bug here must never wedge a session.
    """
    try:
        event = json.loads(payload)
    except ValueError:
        return 0, ""
    if not isinstance(event, dict) or event.get("tool_name") not in ("Agent", "Task"):
        return 0, ""
    spec = event.get("tool_input")
    if not isinstance(spec, dict) or spec.get("subagent_type") == "fork":
        return 0, ""
    model = spec.get("model")
    if isinstance(model, str) and model.strip():
        return 0, ""
    return 2, ("gigabite: this agent names no model, so it would silently inherit one. "
               "Choose one and re-issue the call with `model` set (core.md §6): "
               "\"sonnet\" for search, fan-out, mechanical edits and summaries; "
               "\"opus\" for architecture, security review, ambiguous judgement calls and "
               "the final QA gate.\n")


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def run(name: str) -> int:
    """`gigabite hook NAME`: read the payload, act, never fail the turn."""
    try:
        # No payload when run by hand from a terminal, where reading would wait for one.
        payload = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        if name == "agent-model":
            code, err = agent_model(payload)
            if err:
                sys.stderr.write(err)
            return code
        out = recall(payload) if name == "recall" else refresh()
        if out:
            print(out)
    except Exception:
        pass
    return 0


if __name__ == "__main__" and sys.argv[1:2] == ["refresh-worker"]:
    try:
        refresh_worker(Path(sys.argv[2]), sys.argv[3:])
    except Exception:
        pass
