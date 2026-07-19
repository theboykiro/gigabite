# Granola integration

## Current path: supply notes manually  ✅

Granola v6 encrypts its entire local store (`granola.db` + the `*.enc` caches)
behind a macOS keychain item (`Granola Safe Storage` / `Granola Key`). Reading that
key triggers a one-time macOS authorization prompt, so the store can't be read
unattended. The reliable, secret-free path is to drop exports in:

```
~/.knowledge/_inbox/granola/
```

Accepted: `.md`, `.txt`, `.json`. Both **notes and transcript** are indexed. YAML
front-matter (`title:`, `date:`, `project:`) is picked up when present. Then:

```bash
gigabite ingest        # or /search in Claude Code, which refreshes first
```

To export from Granola: select a meeting (or several) → export as Markdown → drop
the files in the inbox. Re-dropping newer versions updates the index in place.

## Future path: API access  🔜

When you have Granola API access, the clean integration is a direct pull from
their public API (the app already advertises `public_api_user_notes_enabled` /
`public_api_folders_enabled`). The parsing is already built and reused:
`gigabite/sources/granola.py::document_from_granola_json` handles Granola's
document shape (notes + speaker-segmented transcript), so wiring the API is just:
fetch documents → feed each through that function → `store.upsert_document`.

Drop the API key into the keychain (never a file), e.g.:

```bash
security add-generic-password -s "gigabite:granola" -a "$USER" -w
```

…and add a `pull_via_public_api(api_key)` in a `granola_live.py` sibling. Left as a
one-function addition rather than guessing the endpoint schema now.

## Dormant: local keychain decrypt  🧪 (experimental, unverified)

`gigabite granola-connect` attempts the keychain → decrypt → index flow directly.
It is **not verified** — Granola's at-rest format isn't published, so it tries the
known Electron/Chromium patterns (safeStorage AES-CBC, raw-key AES-GCM) and prints
precise diagnostics if none match. It only runs when *you* invoke it and approve the
keychain prompt. Treat it as a spike; the manual path above is the supported one.

```bash
gigabite granola-connect --diagnose   # tries to decrypt, indexes nothing, reports what it saw
```
