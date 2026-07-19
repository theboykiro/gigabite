---
description: Search everything you've discussed — Claude Code, Claude.ai, and Granola meetings
argument-hint: <what to search for>
allowed-tools: Bash(__GIGABITE_BIN__:*)
---
The user wants to search their entire conversation history for: **$ARGUMENTS**

Refresh the index (fast, incremental) and search:

!`__GIGABITE_BIN__ ingest >/dev/null 2>&1; __GIGABITE_BIN__ search "$ARGUMENTS" --limit 15`

From the results above, give a tight, ranked answer:
- For each relevant conversation: one line — title, source, date — and why it matches.
- If a result directly answers the question, quote the key line.
- Group obvious themes; don't repeat near-duplicates.
- Offer to open any conversation in full: `gigabite doc <doc_id>`.
- If there are no results, say so plainly and suggest broader terms.

Do not restate the question or pad the answer.
