---
description: File a Granola transcript you've copied to the clipboard into gigabite (no terminal)
argument-hint: (optional — title/date are read from the transcript header)
allowed-tools: Bash(__GIGABITE_BIN__:*)
---
The user copied a Granola meeting transcript to their clipboard. File it via
gigabite — **do not ask them to paste it into chat, and do not read it into the
conversation**; the CLI reads the clipboard itself and extracts the title and date
from the transcript's own header (`Meeting Title:` / `Date:`).

Usual case — they typed just `/granola` with no arguments. Run:

!`__GIGABITE_BIN__ paste`

Only if `$ARGUMENTS` is non-empty (they want to override the header) pass what they
gave: a title, and/or a date (resolve relative dates like "last monday" to
`YYYY-MM-DD`), as `--title "…"` / `--date "…"`. Project is auto-detected — only pass
`--project` if they named one.

Then report the one-line confirmation the tool prints (title · project · date). If it
says the clipboard is empty, tell them to copy the transcript in Granola first and
re-run `/granola`.
