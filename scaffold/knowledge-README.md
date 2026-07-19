# ~/.knowledge

Your local knowledge base and conversation search index. **Local only** —
iCloud-backed, never pushed to GitHub or any web surface.

## Layout

```
~/.knowledge/
  .index/gigabite.db        the full-text search index (SQLite + FTS5)
  _inbox/
    claude_ai/              drop your Anthropic data export here (.zip or conversations.json)
    granola/                drop Granola exports here (.md / .txt / .json)
  _historical/              reserved for decayed context (ARCHITECTURE §6; not active yet)
  {project}/{layer}/        per-project knowledge (created as you add projects)
```

## What's indexed for search

- **Claude Code** — every session under `~/.claude/projects/`, ingested automatically.
- **Claude.ai** — whatever you drop into `_inbox/claude_ai/` (browser chats can't be
  pulled programmatically; export them from claude.ai → Settings → Privacy → Export).
- **Granola** — exports dropped into `_inbox/granola/`, or a live pull via
  `gigabite granola-connect` (experimental — see GRANOLA.md in the repo).

## Everyday use

```
gigabite ingest            # refresh the index (incremental, fast)
gigabite search "..."      # search everything
gigabite status            # what's indexed
gigabite doc <doc_id>      # open a full conversation
```

Or from Claude Code, anywhere: `/search <query>` and `/recall-status`.
