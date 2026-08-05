# Synthesis and decay

Synthesis and decay are the two background loops that sit on top of the gigabite
search index. Between them they answer the question every knowledge base eventually
runs into: what keeps this from turning into an undifferentiated pile that nobody
trusts? Synthesis pulls what changed up into the knowledge base. Decay pushes what
stopped mattering out of the default view.

Both are built to the same two constraints. They keep the active context lean, and
they never mutate the operating system behind your back. Both are implemented in
`gigabite/features/`, in pure standard library, operating only through the `Store`
API — synthesis in `synthesis.py` (`ARCHITECTURE.md` §5) and decay in `decay.py`
(§6).

---

## Daily synthesis

Synthesis is a feedback loop that turns the day's conversations into *proposed*
knowledge updates. It runs in two halves, and the module described here is only the
first, deterministic half.

The first step is **collect and compress**. `build_digest(store, since_days=1)`
gathers the documents updated within the window, judged by `updated_utc`, groups them
by project, and keeps a short excerpt of roughly four hundred characters from each —
not the full text. Volume stays low deliberately, for the reason set out in
`ARCHITECTURE.md` §6: transcripts live in the source tool, and the knowledge base
holds distilled context only. A digest that quoted everything would simply move the
volume problem one level up.

The second step is **propose**. `write_proposal(store, since_days=1)` renders the
digest to `~/Knowledge/.gigabite/proposals/YYYY-MM-DD.md`, with two empty approval checklists
appended: one headed *Proposed knowledge updates* and one headed *Proposed core.md
updates*. Writing twice in the same day overwrites the file, so the operation is
idempotent and the scheduled job can run as often as it likes.

The third step is **extract and apply**, and it happens later, by hand. Claude reads
the digest, fills in the checklists with the material changes it finds — decisions
made, new constraints, shifted priorities, assumptions validated or invalidated — and
you apply the accepted items yourself.

From the command line, `gigabite synthesize` writes the proposal, `--print` shows the
digest without writing anything, `--list` enumerates the proposals you already have,
and `--since-days N` widens the window.

### Why the gate is non-negotiable

The synthesis module contains **no LLM calls** and **never writes to `core.md` or to
the knowledge base**. Its only output is a proposal file inside `.gigabite/`, which is
machinery rather than knowledge and which the notes ingester skips entirely — so a
proposal cannot become knowledge by sitting there, only by your applying it.

Nothing is applied to the operating system until a human — or a `/gg` session working
on your behalf — reviews the proposal and applies the accepted items. This is
structural rather than a matter of care: the module has no code path that could write
elsewhere, so the guarantee does not depend on anybody remembering it. Automated
writes to an operating protocol without review is exactly the silent drift this design
exists to prevent, and a protocol that has been edited without your knowledge is worse
than no protocol at all, because you will keep trusting it.

---

## Reference-frequency decay

Active context should be a function of what you actually touch, not of everything ever
recorded. Decay enforces that by archiving untouched documents — and it does so by
flipping a flag in the index, never by deleting anything.

**Last-touch** is `accessed_utc`, falling back to `updated_utc` and then
`created_utc`: the most recent signal that a document still matters.
`run(store, window_days=30, dry_run=True)` finds active documents whose last-touch is
older than the window and, when it is not a dry run, archives each one via
`store.set_active(doc_id, False)`. Documents with no parseable timestamp are left
active, on the principle that we never archive what we cannot date.

**Archived is not gone.** Archived rows stay in the index and remain searchable on
explicit request through `search(..., include_historical=True)` — `gigabite search
--all` from the command line — and are excluded only from default loads. The
command-line search goes further: when a normal search finds nothing, it retries
across archived documents automatically and tells you it did so.

**Restoring happens by itself.** Every default search hit calls `Store.record_access`,
which sets `active = 1` and refreshes `accessed_utc`, so re-touching an archived
document brings it back without any action on your part. `restore(store, doc_id)`, or
`gigabite decay --restore <doc_id>`, does the same thing explicitly.

**Inspecting the state** is `status(store)`, or `gigabite decay --status`, which
reports the active and archived split along with the stalest active documents. Running
`gigabite decay` without `--apply` is a dry run and prints what it would archive.

### On the thirty-day window

`ARCHITECTURE.md` §6 names fourteen days as a starting point. The implementation opens
wider, at thirty, on purpose: a conservative window produces fewer surprises while the
system is still young, and it is easier to tighten a window than to explain why
something useful vanished from default search. Tune it down against real usage. Because
the window is a parameter on every call rather than a constant in the logic, tuning it
is a configuration change and not a code change.

---

## Taken together

The two loops pull in opposite directions and that is the design. Synthesis adds
signal, one gated proposal at a time. Decay removes noise, reversibly and without
deleting anything. Neither can act on the knowledge base or the operating protocol
without you: synthesis because it structurally cannot write there, decay because
everything it does is a flag that a single search will flip back. If you ignore both
for a month, the worst outcome is a folder of unread proposals and a slightly narrower
default search — which is the correct failure mode for a system that is supposed to
work when you are busy.
