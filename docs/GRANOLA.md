# Granola integration

Granola holds the raw material this whole system is most interested in: the reasoning
that happens out loud in meetings and then evaporates. Getting it into the index is
therefore worth some awkwardness, and there is some, because Granola does not want to
be read by anything other than Granola.

This document sets out the three routes in order of how much you should rely on them.
The first is the one to use. The second is what to build when API access appears. The
third is an experiment that has not been shown to work.

## The supported route: supply the notes yourself

Granola v6 encrypts its entire local store — `granola.db` and the `*.enc` caches —
behind a macOS keychain item (`Granola Safe Storage` / `Granola Key`). Reading that key
triggers a one-time macOS authorisation prompt, which means the store cannot be read
unattended by anything, gigabite included. The reliable, secret-free route is
consequently to hand the notes over.

The quickest version needs no terminal at all. Copy the transcript in Granola and run
`/granola` in Claude Code. The command reads your clipboard directly, pulls the title
and date out of the transcript's own header (`Meeting Title:` and `Date:`, with a
date lacking a year assumed to be this one), auto-detects the project, files the
meeting, and indexes it — all without the transcript passing through the conversation.
`gigabite paste` is the same thing from a shell.

For a meeting you have exported as a file, drop it into `~/Knowledge/Inbox/` — or into
`~/Knowledge/Inbox/<project>/meetings/` if you want to name the project explicitly —
and run `gigabite file`. It is filed as a readable note in that project's `meetings/`
layer, alongside anything you wrote by hand.

For a batch, export the meetings from Granola as Markdown and drop the files straight
into `~/Knowledge/_sources/granola/`, then run:

```bash
gigabite ingest        # or /search in Claude Code, which refreshes first
```

That folder accepts `.md`, `.txt`, and `.json`. Both the notes **and** the transcript
are indexed, and YAML front matter (`title:`, `date:`, `project:`) is picked up when
present. Re-dropping a newer version of a meeting updates the existing entry in place
rather than creating a duplicate.

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
`granola_live.py`. This has been left as a one-function addition rather than written
speculatively, because guessing at an endpoint schema you cannot test against produces
code that looks finished and is not.

## The dormant route: local keychain decryption

`gigabite granola-connect` attempts the keychain → decrypt → index flow directly
against the local store. It is **not verified**. Granola's at-rest format is not
published, so the connector tries the known Electron and Chromium patterns —
`safeStorage` AES-CBC, raw-key AES-GCM — and prints precise diagnostics when none of
them match. It runs only when you invoke it and only after you approve the keychain
prompt yourself.

```bash
gigabite granola-connect --diagnose   # attempts decryption, indexes nothing, reports what it saw
```

Treat this as a spike. It is kept in the tree because the diagnostics are useful if
Granola's format is ever documented, not because it is a path anyone should depend on.

## Which to use

Use the manual route. It has no secrets, no dependency on an undocumented format, and
no way to break when Granola ships an update — and the `/granola` clipboard path costs
about three seconds per meeting, which is cheap enough that the discipline it requires
is not really discipline at all. Revisit the API route when you actually have access,
and leave the decryption spike where it is.
