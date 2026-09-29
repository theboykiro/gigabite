#!/usr/bin/env bash
# gigabite PreToolUse hook — every agent spawn names its model.
# core.md §6 says to pick the model tier by the cost of being wrong. As prose that is
# a request, not a mechanism: an Agent call with no `model` silently inherits the
# session's model, and nobody chose it. This hook refuses that call and tells Claude
# to choose, so the choice is made on every spawn — there is deliberately no default.
#
# Contract with Claude Code (PreToolUse, matcher Agent|Task):
#   · exit 2 + stderr → the call is blocked and stderr goes back to Claude to retry
#   · exit 0          → the call proceeds
# A fork copies the parent's model and ignores `model`, so it is let through. Anything
# this hook can't read (no payload, bad JSON, another tool) proceeds: a bug here must
# never wedge a session.

[ -t 0 ] && exit 0

/usr/bin/python3 -c '
import json, sys
try:
    event = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(event, dict) or event.get("tool_name") not in ("Agent", "Task"):
    sys.exit(0)
spec = event.get("tool_input")
if not isinstance(spec, dict) or spec.get("subagent_type") == "fork":
    sys.exit(0)
model = spec.get("model")
if isinstance(model, str) and model.strip():
    sys.exit(0)
sys.stderr.write(
    "gigabite: this agent names no model, so it would silently inherit one. "
    "Choose one and re-issue the call with `model` set (core.md §6): "
    "\"sonnet\" for search, fan-out, mechanical edits and summaries; "
    "\"opus\" for architecture, security review, ambiguous judgement calls and "
    "the final QA gate.\n")
sys.exit(2)
'
