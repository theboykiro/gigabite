#!/usr/bin/env bash
# gigabite UserPromptSubmit hook — ambient recall.
# On each prompt, retrieve strongly-relevant prior context from the local index
# and inject it so the conversation is answered WITH your history loaded.
# Silent unless a hit genuinely matches the prompt, so it never derails other work.
#
# The work is `gigabite hook recall` (gigabite/hooks.py), which runs the same on
# macOS and Windows; this wrapper keeps the path an install already registered.
# Reads the hook JSON on stdin; prints a compact recall block to stdout (which
# Claude Code adds to the turn's context). Never fails the prompt: always exit 0.

GIGABITE_BIN="__GIGABITE_BIN__"

"$GIGABITE_BIN" hook recall 2>/dev/null
exit 0
