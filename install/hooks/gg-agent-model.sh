#!/usr/bin/env bash
# gigabite PreToolUse hook — every agent spawn names its model.
# core.md §6 says to pick the model tier by the cost of being wrong; this refuses an
# Agent call with no `model` and tells Claude to choose (see gigabite/hooks.py).
#
# The work is `gigabite hook agent-model`, which runs the same on macOS and Windows;
# this wrapper keeps the path an install already registered.
# Contract with Claude Code (PreToolUse, matcher Agent|Task):
#   · exit 2 + stderr → the call is blocked and stderr goes back to Claude to retry
#   · exit 0          → the call proceeds
# Anything else the launcher does (gone, crashed, or an older gigabite that has no
# `hook` and exits 2 from argparse) proceeds: a bug here must never wedge a session.

GIGABITE_BIN="__GIGABITE_BIN__"

[ -t 0 ] && exit 0

err=$("$GIGABITE_BIN" hook agent-model 2>&1 >/dev/null)
rc=$?
case "$err" in
  "gigabite: this agent names no model"*) ;;
  *) exit 0 ;;
esac
[ "$rc" -eq 2 ] || exit 0
printf '%s\n' "$err" >&2
exit 2
