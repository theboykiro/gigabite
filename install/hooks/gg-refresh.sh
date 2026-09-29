#!/usr/bin/env bash
# gigabite SessionStart hook — keeps the index fresh.
# When a Claude Code session starts, run an incremental `gigabite ingest` fully
# detached in the background, so the work from earlier today is recallable in this
# session. Replaces the old 18:00 launchd job: the index is refreshed when it is
# about to be used, not at a fixed hour when the Mac may be asleep.
#
# Contract with Claude Code: return at once, print nothing (SessionStart stdout is
# added to the session's context), and never fail the session — always exit 0.
#   · one exception to "print nothing": the first session after a first install
#     prints a one-time notice of what the install made (see below)
#   · one refresh at a time: a second session starting mid-refresh skips it (flock,
#     released by the kernel if the refresh dies, so a crash can't wedge it)
#   · time-boxed: a refresh still running after 15 minutes is stopped
#   · output: <knowledge>/.gigabite/refresh.log, trimmed to the last ~128 KB

GIGABITE_BIN="__GIGABITE_BIN__"

# Drain the hook payload; nothing in it is needed. Not when run by hand from a
# terminal, where there is no payload and `cat` would wait for one.
[ -t 0 ] || cat >/dev/null 2>&1

# Mirrors gigabite/config.py: the machinery folder under the knowledge base.
KNOW_DIR="${GIGABITE_KNOWLEDGE_DIR:-$HOME/Knowledge}"
DATA_DIR="$KNOW_DIR/.gigabite"

# The launcher is gone (repo moved or deleted): nothing to run, nothing to create.
[ -x "$GIGABITE_BIN" ] || exit 0

# First session after a first install: say once what the install made. When Claude
# Code ran the installer, the user saw only its summary, and this session is the one
# step every install reaches, because a restart is required. install.sh leaves the
# marker; claiming it by rename means two sessions starting together print it once.
# `systemMessage` is shown to the user; `additionalContext` reaches Claude, in case
# the client does not show the former and so Claude can answer questions about it.
if mv "$DATA_DIR/welcome-pending" "$DATA_DIR/welcome-shown" 2>/dev/null; then
  /usr/bin/python3 - "$KNOW_DIR" "${GIGABITE_CORE_DIR:-$HOME/.core}/core.md" "$HOME" \
    2>/dev/null <<'PY'
import json, sys

know, core, home = sys.argv[1:4]
tilde = lambda p: "~" + p[len(home):] if p.startswith(home + "/") else p
try:
    with open(core, encoding="utf-8", errors="replace") as fh:
        unfilled = "[FILL]" in fh.read()
except OSError:
    unfilled = False

k = tilde(know)
message = (f"gigabite is set up. It made {k}, the folder for your notes, meeting "
           f"transcripts and files, one subfolder per project. Everything in it is "
           f"searchable from Claude Code. What goes where: {k}/README.md")
if unfilled:
    message += (f"\nYour working rules for Claude are in {tilde(core)}. "
                f"Run /core-setup to fill them in.")
context = ("[gigabite: first session since install] The user may not know what the "
           "install created. At the start of your first reply, pass this on in two or "
           "three plain sentences, once:\n" + message)
print(json.dumps({"systemMessage": message,
                  "hookSpecificOutput": {"hookEventName": "SessionStart",
                                         "additionalContext": context}}))
PY
fi

/usr/bin/python3 - "$GIGABITE_BIN" "$DATA_DIR" >/dev/null 2>&1 <<'PY'
import os, sys

gb, data = sys.argv[1], sys.argv[2]

# Double fork + setsid: the hook's own process exits now, and the refresh lives in a
# session of its own with no handle on Claude Code's pipes, so nothing waits on it
# and nothing that tears down the hook's process group can reach it.
if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    os._exit(0)
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
import fcntl, subprocess, time           # after the fork: nothing waits on these

try:
    os.makedirs(data, exist_ok=True)
    lock = open(os.path.join(data, "refresh.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os._exit(0)                      # another session is already refreshing
    log_path = os.path.join(data, "refresh.log")
    try:
        if os.path.getsize(log_path) > 262144:
            with open(log_path, "rb") as fh:
                fh.seek(-131072, os.SEEK_END)
                tail = fh.read()
            with open(log_path, "wb") as fh:
                fh.write(tail)
    except OSError:
        pass
    stamp = lambda: time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as log:
        log.write(f"{stamp()}  refresh: {gb} ingest\n")
        log.flush()
        try:
            rc = subprocess.run([gb, "ingest"], stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, timeout=900).returncode
        except subprocess.TimeoutExpired:
            rc = "timed out after 900s"
        log.flush()
        log.write(f"{stamp()}  refresh done (exit {rc})\n")
except Exception:
    pass
os._exit(0)
PY
exit 0
