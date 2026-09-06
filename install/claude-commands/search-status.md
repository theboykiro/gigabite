---
description: Show what's indexed in your gigabite conversation search (counts, sources, date span)
allowed-tools: Bash(__GIGABITE_BIN__:*)
---
<!-- gigabite:managed — this file is reinstalled by gigabite's install.sh. Edits here are replaced on the next run; rename it to keep your own version. -->
Refresh and report the state of the local conversation index:

!`__GIGABITE_BIN__ ingest 2>&1; echo; __GIGABITE_BIN__ status`

Summarize for the user: how many conversations/meetings are indexed per source,
the date span covered, and anything that just changed. If a source is empty, say
how to populate it — Claude.ai: run the browser export into
`~/Knowledge/.gigabite/imports/claude_ai/`, then `gigabite ingest && gigabite materialize`.
Meetings: export the meeting into `~/Knowledge/<project>/meetings/`. Keep it to a
few lines.
