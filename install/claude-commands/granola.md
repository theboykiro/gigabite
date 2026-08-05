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

The transcript is written straight into `~/Knowledge/<project>/meetings/` — exactly
where a Finder drop of the same export would land — and indexed in the same pass. So
report the note path the tool prints: that is where the meeting now lives, and it is
the same path either route.

Then report the one-line confirmation the tool prints (title · project · date). If it
says the clipboard is empty, tell them to copy the transcript in Granola first and
re-run `/granola`. If it says the file is unfiled, no project could be worked out and
the note is at the top of `~/Knowledge`: offer to re-run with `--project <name>`, and
do not guess a project for them.

If the user pastes a transcript into the chat anyway, use
`__GIGABITE_BIN__ paste --stdin`; if they hand over a transcript *file*, use
`__GIGABITE_BIN__ add <path>`. Do not save it by any other means and never write into
`~/Knowledge` yourself — those commands are the one intake path, and they file it in
the same place in the same shape whichever way it reached you.
