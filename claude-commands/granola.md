---
description: File a Granola transcript you've copied to the clipboard into gigabite (no terminal)
argument-hint: <meeting title> [and a date like 2026-07-17 or "last monday"]
allowed-tools: Bash(__GIGABITE_BIN__:*), Bash(date:*)
---
The user copied a Granola meeting transcript to their clipboard. Your job is to
file it via gigabite — **do not ask them to paste it into chat, and do not read
it into the conversation**; the CLI reads the clipboard itself.

From `$ARGUMENTS`, work out:
- **title** — the meeting name (everything that isn't a date). If empty, omit `--title`
  and the tool uses the transcript's first line.
- **date** — if they gave one (absolute `YYYY-MM-DD`, or relative like "yesterday",
  "last monday", "friday"), resolve it to `YYYY-MM-DD` (today is `!date +%F`). If none
  given, omit `--date` (defaults to today).

Then run (fill in only the flags you resolved):

!`__GIGABITE_BIN__ paste --title "TITLE_HERE" --date "DATE_HERE"`

Notes:
- Project is auto-detected from the content; don't pass `--project` unless the user
  specified one.
- Report back the one-line confirmation the tool prints (title · project · date).
- If it says the clipboard is empty, tell them to copy the transcript in Granola first,
  then re-run `/granola`.
