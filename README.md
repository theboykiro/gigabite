# gigabite

A single, local **context router** for Claude, run through Claude Code. You talk
to it like an assistant; every turn loads your operating protocol (`~/.core/core.md`),
detects the project you're in, and recalls relevant context from everything you've
discussed — **Claude Code** sessions, **Claude.ai** chats (in and out of projects),
**Granola** meetings, your own **notes**, and **calendar** — then answers with that
history loaded. It can also spawn SOP-driven subagents for build/research/review work.

Pure Python standard library. No server, no embeddings, no cloud. Nothing leaves
the device: the index is a local SQLite file, content lives under `~/.knowledge/`,
and only code is in this repo (see [`initial-plan/`](initial-plan/) for the design).

---

## Install

```bash
./install.sh
```

Idempotent and non-destructive. Creates the `~/.core` and `~/.knowledge` layout
(never overwriting your content), puts `gigabite` on your PATH, installs the Claude
Code commands (`/gg`, `/search`, `/recall-status`, `/calendar`) and subagents,
wires the router protocol + ambient recall hook, and builds the initial index.

Requirements: macOS, Python 3.9+ (system Python is fine). Nothing to `pip install`.

### On a new machine

```bash
git clone <your-repo-url> && cd giga-bite && ./install.sh
```

Code comes from git; your content (`~/.core`, `~/.knowledge`) does not — it's local
and rebuilt as you add sources. Re-run `./install.sh` any time to update.

## Use

**Talk to it (primary).** In Claude Code, just type — ambient recall injects your
history into every turn. For an explicit routed answer:

```
/gg what's open on the acme traffic drop?      # loads core + recalls + answers
/calendar                                          # after pasting a calendar screenshot
/search <query>        /recall-status
```

**Terminal:**

```bash
gigabite search "enterprise pricing anchor"      # search everything
gigabite search "budget" --source granola         # filter by source
gigabite search "roadmap" --project acme --all  # scope to a project; --all incl. archived
gigabite doc <doc_id>                              # open a full conversation
gigabite save "Decided X because Y" -p acme -l delivery -t "Title"   # persist a note
gigabite project add acme --keywords "acme, ej"                   # define a project
gigabite calendar agenda --day today               # meetings + attached prep
gigabite synthesize                                # gated end-of-day proposal
gigabite decay --status                            # reference-frequency archiving
gigabite ingest                                    # refresh the index (local, incremental)
gigabite status
```

## What gets indexed

| Source | How |
|---|---|
| **Claude Code** | every session in `~/.claude/projects/`, automatically on `ingest` |
| **Claude.ai** | run `scripts/claude-ai-safari-export.js` in the claude.ai console → drop the downloaded `conversations.json` into `~/.knowledge/_inbox/claude_ai/` → `gigabite ingest` |
| **Granola** | export a meeting as Markdown → `~/.knowledge/_inbox/granola/` → `gigabite ingest` |
| **Notes** | `gigabite save …` (routes to `~/.knowledge/{project}/{layer}/`) |
| **Calendar** | paste a screenshot in Claude Code → `/calendar` |

**claude.ai** is pulled from *inside the browser* because its API is Cloudflare-gated
for terminal clients — the in-page script carries your real session, and produces a
`conversations.json` the importer understands (projectless + project chats, tagged).
See [`CLAUDE_AI.md`](CLAUDE_AI.md). A keychain-token/API path exists (`claude-login`/
`claude-sync`) but Cloudflare blocks it; the browser export is the working route.

<!-- legacy note retained below -->
<!--
¹ Uses claude.ai's internal (undocumented) endpoints — unofficial, may change, and
automated access is a grey area under claude.ai's terms. It's your own data.
-->

Granola's local store is encrypted behind a macOS keychain key, so notes are
supplied manually (export → inbox). A public-API path is documented for when you
have API access — see [`GRANOLA.md`](GRANOLA.md).

Adding a source is dropping a file and running `gigabite ingest`. Re-dropping a
newer export updates in place; unchanged files are skipped. Untouched documents
decay to an archive after 30 days (non-destructive; a matching search restores them).

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
