# Meetings

Meetings hold the raw material this whole system is most interested in: the reasoning
that happens out loud and then evaporates. Getting it into the index is therefore
worth some awkwardness, and there is a little, because a meeting only arrives when
you hand it over.

The source is called `meeting`, after the content rather than after whatever recorded
it. The parsing itself never reads another application's store: what the ingester
does is parse the markdown, text or JSON handed to it — a Granola export is one thing
that can be, and so is anything else that produces prose. There is now one opt-in
exception to "nothing here talks to a meeting-notes app" — a daily pull from
Granola's own public API, for anyone on Granola Business — described below.

This document sets out the supported manual route, then the live pull.

## The supported route: supply the notes yourself

Nothing in gigabite reads a meeting app's local store. That is a deliberate boundary
rather than a limitation to work around: a route with no credential in it, and no
dependency
on another application's internal format, cannot break when that application ships
an update — and this is a path used several times a day, so it has to be the boring
kind of reliable.

There is one destination, `~/Knowledge/<project>/meetings/`, and the two routes below
differ only in how the text gets there. A meeting ends up as a readable note in its
project's folder whichever one you use, so "where did that meeting go?" has a single
answer.

The quickest version needs no terminal at all. Copy the transcript in whatever took
it — Granola, say — and run `/meeting` in Claude Code. The command reads your
clipboard directly, pulls the title and date out of the transcript's own header
(`Meeting Title:` and `Date:`, with a date lacking a year assumed to be this one),
detects the project, and writes and indexes the note in the same pass — all without
the transcript passing through the conversation. `gigabite paste` is the same thing
from a shell, and both are, quite literally, the Finder drop below with the drag done
for you.

For a meeting you have exported as a file, save it into
`~/Knowledge/<project>/meetings/` and you are done: the next `gigabite ingest` indexes
it where it sits, and the folder it is in *is* its project and layer. For a batch,
export them all as Markdown and move the lot in at once; `.md`, `.txt`, `.vtt`, and
`.json` are all read for their prose. If you would rather not pick the folder
yourself, `gigabite add <file>` routes it by keyword — and if no project resolves, the
file is left loose at the top of `~/Knowledge` for you to drag in, never filed into a
guess.

## The live route: a daily pull from Granola's public API

Granola Business unlocks a real, documented API at
`https://public-api.granola.ai/v1`, authenticated with an API key you create yourself
(Granola desktop app → Settings → Connectors → API keys). `gigabite/sources/
granola_live.py` pulls every new note once a day, at 19:00, and mirrors
`claude_ai_live.py`'s pattern throughout: the key lives only in the macOS keychain
(`gigabite:granola`), read at runtime and never printed, logged, or accepted as a CLI
argument.

One command stores the key, the other runs the pull:

```bash
gigabite granola-login   # opens the macOS secure prompt; paste the key there
gigabite granola-sync    # pulls new notes, files each one, prints a report
```

`granola-sync` does three things, in order:

1. **Pull.** `GET /notes?created_after=<last successful pull>` (cursor-paginated),
   then `GET /notes/{id}?include=transcript` for each one — full transcript and the
   AI summary, which folds in your own typed notes (Granola merges them before the
   API ever sees the note). A note whose summary hasn't finished yet 404s on the
   detail endpoint; that is treated as "not ready, retry tomorrow", not an error,
   which is exactly what happens to a same-day meeting pulled at the 19:00 mark.
2. **Write.** Each note's raw JSON is written to `~/Knowledge/.gigabite/imports/
   meetings/<note_id>.json` — kept deliberately, the same as every other raw import,
   so the index can be rebuilt from scratch. Parsing that JSON is not
   reimplemented: it is handed straight to the existing
   `sources/meetings.py::document_from_granola_json` / `meetings.ingest`, unchanged.
3. **Route.** `document_from_granola_json` sets `project=""` on purpose — nothing
   here ever trusts whatever folder or workspace Granola itself assigned. Once the
   note is indexed, `granola-sync` calls `features.materialize.run(store,
   source="meeting")`, which resolves a project the same way every other unlabelled
   document does: keyword matching against each project's `_project.md`, over the
   full transcript and summary. A note that matches nothing stays visibly unresolved
   in the index rather than being guessed at or dumped in `personal/`.

To run it automatically, load the LaunchAgent — a separate, additive install path
from the main daily job, since not everyone has Granola API access:

```bash
sed -e "s|__DAILY_BIN__|$PWD/bin/gigabite-granola-pull|g" \
    -e "s|__LOG__|$HOME/Library/Logs/gigabite-granola-pull.log|g" \
    install/launchd/com.gigabite.granola-pull.plist \
    > ~/Library/LaunchAgents/com.gigabite.granola-pull.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.gigabite.granola-pull.plist
```

## Which to use

Both. The manual route (`/meeting`, `gigabite paste`, dropping a file) has no
secrets and no dependency on an API that could change, and stays the right choice
for a meeting you want indexed right now rather than at 19:00. The live pull is for
not having to remember — once the key is in the keychain and the LaunchAgent is
loaded, every meeting from the day is transcript-plus-summary in its project folder
by evening, with nothing to copy or paste.
