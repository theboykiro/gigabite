# gigabite

One local search layer over everything you've discussed — your **Claude Code**
sessions, your exported **Claude.ai** chats, and your **Granola** meeting notes —
searchable from the terminal or from inside Claude Code, anywhere.

Pure Python standard library. No server, no embeddings, no cloud. Nothing leaves
the device: the index is a local SQLite file, and content lives under `~/.knowledge/`,
never in this repo.

> This is the first working slice of the larger **Context Router** design in
> [`initial-plan/`](initial-plan/): the always-loaded `core.md`, the `~/.knowledge/`
> layout, and full-text recall across sources. Detection/routing, daily synthesis,
> and decay are specced there and not built yet.

---

## Install

```bash
./install.sh
```

Idempotent and non-destructive. It creates the `~/.core` and `~/.knowledge` layout
(without overwriting anything you already have), puts `gigabite` on your PATH,
installs the `/search` and `/recall-status` Claude Code commands, and builds the
initial index.

Requirements: macOS, Python 3.9+ (system Python is fine). Nothing to `pip install`.

## Use

From the terminal:

```bash
gigabite search "enterprise pricing anchor"   # search everything
gigabite search "budget" --source granola      # filter by source
gigabite search "roadmap" --project acme        # filter by project
gigabite ingest                                  # refresh the index (incremental)
gigabite status                                  # what's indexed
gigabite doc <doc_id>                            # open a full conversation
```

From Claude Code, in any folder:

```
/search enterprise pricing anchor
/recall-status
```

`/search` refreshes the index first, then Claude ranks and summarises the hits.

## What gets indexed

| Source | How | Status |
|---|---|---|
| **Claude Code** | every session in `~/.claude/projects/`, automatically | ✅ working |
| **Claude.ai** | drop your data export into `~/.knowledge/_inbox/claude_ai/` | ✅ working |
| **Granola** | drop `.md`/`.txt`/`.json` exports into `~/.knowledge/_inbox/granola/` | ✅ working (manual) |

Granola's local store is encrypted behind a macOS keychain key, so notes are
supplied manually for now. A live/API path is stubbed for when you have API
access — see [`GRANOLA.md`](GRANOLA.md).

Adding a source is just dropping a file and running `gigabite ingest` (or `/search`,
which refreshes first). Re-dropping a newer export updates in place; unchanged files
are skipped.

## How it works

- **Ingest** normalises each source into *documents* (a conversation / meeting) made
  of *messages* (turns / notes / transcript), with source, project, and timestamps.
- **Index** is one SQLite FTS5 table, one row per message, with the document title
  denormalised on for ranking. Search uses `bm25` (title-weighted) with highlighted
  `snippet()`s. Incremental ingest is tracked by file signature.
- **Storage** — code here; content in `~/.knowledge/`; the operating protocol in
  `~/.core/core.md`; secrets (if ever) in the OS keychain. Only code is meant for GitHub.

```
gigabite/
  config.py          paths & constants (overridable via env)
  store.py           SQLite + FTS5 index, upsert & search
  util.py            text extraction, time parsing, FTS query safety
  ingest.py          orchestrates sources
  sources/
    claude_code.py   ~/.claude/projects/**/*.jsonl
    claude_ai.py     Anthropic data export (.zip / conversations.json)
    granola.py       inbox .md/.txt/.json  (the manual path)
    granola_live.py  experimental keychain-decrypt connector (dormant)
  cli.py             the `gigabite` command
bin/gigabite         self-locating launcher
claude-commands/     /search, /recall-status  (installed to ~/.claude/commands)
scaffold/            templates copied into ~/.core and ~/.knowledge on install
tests/               `python3 -m unittest discover -s tests`
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

No network, no home-directory writes (tests redirect the stores to a temp dir).

## Backup

Only code belongs in git. Content (`~/.core`, `~/.knowledge`) is local and
iCloud-backed. Consider a real versioned backup for the knowledge base too — see
the note in [`initial-plan/ARCHITECTURE.md`](initial-plan/ARCHITECTURE.md).
