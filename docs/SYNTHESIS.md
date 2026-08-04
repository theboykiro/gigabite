# Synthesis & decay

The self-maintaining layer on top of the gigabite search index. Two jobs, both
built to keep the active context lean *and* to never mutate the operating
system behind your back. Implemented in `gigabite/features/`, pure stdlib,
operating only through the `Store` API.

- Synthesis (`gigabite/features/synthesis.py`) — ARCHITECTURE §5
- Decay (`gigabite/features/decay.py`) — ARCHITECTURE §6

---

## Daily synthesis (gated)

A feedback loop that turns the day's conversations into *proposed* knowledge
updates. It runs in two halves, and the module here is only the first,
deterministic half.

1. **Collect + compress (this module).** `build_digest(store, since_days=1)`
   gathers documents updated in the window (by `updated_utc`), groups them by
   project, and keeps a short ~400-char excerpt per document — not full
   transcripts. Volume stays low on purpose (§6): transcripts live in the
   source tool; the digest holds distilled context only.
2. **Propose (this module).** `write_proposal(store, since_days=1)` renders the
   digest to `~/.knowledge/_proposals/YYYY-MM-DD.md` with two empty approval
   checklists: **Proposed knowledge updates** and **Proposed core.md updates**.
   Same-day writes overwrite (idempotent).
3. **Extract + apply (later, by hand).** Claude reads the digest, fills in the
   checklists with the material changes (decisions, new constraints, shifted
   priorities), and the accepted items are applied manually.

### The gate is non-negotiable

This module contains **no LLM calls** and **never writes to `core.md` or the
knowledge base**. Its only output is a proposal file. Nothing is applied to the
operating system until a human (or a `/gg` session) reviews it and applies the
accepted items. Automated writes to the operating protocol without review is
exactly the silent drift the design exists to prevent.

---

## Reference-frequency decay (non-destructive)

Active context should be a function of what you actually touch, not of
everything ever recorded. Decay archives untouched documents by flipping the
index's `active` flag — it never deletes anything.

- **Last-touch** = `accessed_utc`, falling back to `updated_utc`, then
  `created_utc`. The most recent signal that a document still matters.
- **`run(store, window_days=30, dry_run=True)`** finds active documents whose
  last-touch is older than the window and (when not a dry run) archives them
  via `store.set_active(doc_id, False)`. Documents with no parseable timestamp
  are left active — we never archive what we can't date.
- **Archived ≠ gone.** Archived rows stay in the index and remain searchable on
  explicit request (`search(..., include_historical=True)`), just excluded from
  default loads.
- **Restore on access.** Every default search hit calls `Store.record_access`,
  which sets `active = 1` and refreshes `accessed_utc` — so re-touching an
  archived document brings it back automatically. `restore(store, doc_id)` does
  the same explicitly.
- **`status(store)`** reports the active/archived split and the stalest active
  documents.

### Window: 30 days

ARCHITECTURE §6 names **14 days** as a starting point. We open wider (30) on
purpose — conservative, fewer surprises — and tune down against real usage. The
window is a parameter on every call, so tuning is a config change, not a code
change.
