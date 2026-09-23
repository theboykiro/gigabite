# Meetings

A meeting gets into the index as a file in `~/Knowledge/<project>/meetings/`. There are
three ways to put it there.

## Paste it

Copy the transcript from whatever recorded it and run `gigabite paste` (or ask Claude to
run it). It reads the clipboard and takes the title and date from the transcript's own
`Meeting Title:` and `Date:` header. A date with no year is taken as this year. It detects
the project from the text, then writes and indexes the note. The transcript never passes
through the chat. `--project`, `--title` and `--date` override what it detects, and
`--stdin` reads from a pipe instead of the clipboard.

## Drop a file

Save an export (`.md`, `.txt`, `.vtt` or `.json`) into `~/Knowledge/<project>/meetings/`.
It's indexed where it sits the next time the index refreshes. The folder is its project
and layer. `gigabite add <file>` does the same from anywhere, choosing the project by
keyword. If no project matches, the file is left loose at the top of `~/Knowledge` for
you to drag into the right folder. It is never filed into a guess.

## Granola's API (optional)

Granola Business has a documented API. Create a key in the Granola desktop app under
Settings → Connectors → API keys, then run:

```bash
gigabite integrations    # stores the key in the macOS keychain, offers a daily pull at 19:00
```

The installer offers the same step when you run it in a terminal. It can't when you use
the piped one-liner, so run `gigabite integrations` yourself afterwards. The key is kept
only in the keychain (service `gigabite:granola`). It's read when the pull runs, and
never printed, logged or accepted as a command-line argument. `gigabite granola-login`
stores or replaces the key on its own. `gigabite granola-sync` runs a pull now.

A pull fetches notes created since the last successful one, with transcript and AI
summary. It keeps the raw JSON under `~/Knowledge/.gigabite/imports/meetings/`, then
files each meeting by matching the transcript and summary against your projects'
keywords. Granola's own folders are ignored. A meeting that matches no project is
written loose at the top of `~/Knowledge`. Move it rather than delete it: a deleted
loose note is written again on the next sync. A note whose summary isn't ready yet is
retried on the next run.
