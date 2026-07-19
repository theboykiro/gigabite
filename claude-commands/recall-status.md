---
description: Show what's indexed in your gigabite conversation search (counts, sources, date span)
allowed-tools: Bash(__GIGABITE_BIN__:*)
---
Refresh and report the state of the local conversation index:

!`__GIGABITE_BIN__ ingest 2>&1; echo; __GIGABITE_BIN__ status`

Summarize for the user: how many conversations/meetings are indexed per source,
the date span covered, and anything that just changed. If a source is empty,
tell them how to populate it (Claude.ai export → inbox, Granola → export or
`gigabite granola-connect`). Keep it to a few lines.
