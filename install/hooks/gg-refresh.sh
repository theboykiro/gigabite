#!/usr/bin/env bash
# gigabite SessionStart hook — keeps the index fresh.
# When a Claude Code session starts, run an incremental `gigabite ingest` fully
# detached in the background, so the work from earlier today is recallable in this
# session. The first session after a first install also prints a one-time notice
# of what the install made.
#
# The work is `gigabite hook refresh` (gigabite/hooks.py), which runs the same on
# macOS and Windows; this wrapper keeps the path an install already registered.
# Contract with Claude Code: return at once, print nothing but that notice, and
# never fail the session — always exit 0.

GIGABITE_BIN="__GIGABITE_BIN__"

# The launcher is gone (repo moved or deleted): nothing to run, nothing to create.
# Drain the payload first, unless run by hand from a terminal, where there is none
# and `cat` would wait for one.
if [ ! -x "$GIGABITE_BIN" ]; then
  [ -t 0 ] || cat >/dev/null 2>&1
  exit 0
fi

"$GIGABITE_BIN" hook refresh 2>/dev/null
exit 0
