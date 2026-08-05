# Granola integration

Granola holds the raw material this whole system is most interested in: the reasoning
that happens out loud in meetings and then evaporates. Getting it into the index is
therefore worth some awkwardness, and there is a little, because Granola content
arrives only when you hand it over.

This document sets out the supported route, then the one to build when API access
appears.

## The supported route: supply the notes yourself

Nothing in gigabite reads Granola's local store. That is a deliberate boundary rather
than a limitation to work around: a route with no credential in it, and no dependency
on another application's internal format, cannot break when that application ships an
update — and this is a path used several times a day, so it has to be the boring kind
of reliable.

There is one destination, `~/Knowledge/<project>/meetings/`, and the two routes below
differ only in how the text gets there. A meeting ends up as a readable note in its
project's folder whichever one you use, so "where did that meeting go?" has a single
answer.

The quickest version needs no terminal at all. Copy the transcript in Granola and run
`/granola` in Claude Code. The command reads your clipboard directly, pulls the title
and date out of the transcript's own header (`Meeting Title:` and `Date:`, with a date
lacking a year assumed to be this one), detects the project, and writes and indexes
the note in the same pass — all without the transcript passing through the
conversation. `gigabite paste` is the same thing from a shell, and both are, quite
literally, the Finder drop below with the drag done for you.

For a meeting you have exported as a file, save it into
`~/Knowledge/<project>/meetings/` and you are done: the next `gigabite ingest` indexes
it where it sits, and the folder it is in *is* its project and layer. For a batch,
export them all as Markdown and move the lot in at once; `.md`, `.txt`, `.vtt`, and
`.json` are all read for their prose. If you would rather not pick the folder
yourself, `gigabite add <file>` routes it by keyword — and if no project resolves, the
file is left loose at the top of `~/Knowledge` for you to drag in, never filed into a
guess.

## The future route: the public API

When you have Granola API access, the clean integration is a direct pull from their
public API — the app already advertises `public_api_user_notes_enabled` and
`public_api_folders_enabled`, so the capability exists on their side.

Most of the work is already done. `gigabite/sources/granola.py::document_from_granola_json`
handles Granola's document shape, including notes and the speaker-segmented
transcript, and it is reused rather than rewritten. Wiring the API is therefore three
steps: fetch the documents, feed each through that function, and call
`store.upsert_document`.

The API key goes in the keychain and never in a file:

```bash
security add-generic-password -s "gigabite:granola" -a "$USER" -w
```

Then add a `pull_via_public_api(api_key)` alongside the existing code in
`sources/granola.py`. This has been left as a one-function addition rather than
written speculatively, because guessing at an endpoint schema you cannot test against
produces code that looks finished and is not.

## Which to use

Use the manual route. It has no secrets, no dependency on an undocumented format, and
no way to break when Granola ships an update — and the `/granola` clipboard path costs
about three seconds per meeting, which is cheap enough that the discipline it requires
is not really discipline at all. Revisit the API route when you actually have access.
