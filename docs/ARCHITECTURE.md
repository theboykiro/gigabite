# Architecture

This document is the technical design of gigabite: what the pieces are, how they fit
together, and why each of them is shaped the way it is. It is the companion to the
root `README.md`, which tells you how to use the tool; this one tells you how it
works and which decisions are load-bearing.

It is written in the order the system actually runs. Constraints first, because they
explain everything downstream. Then the context stack, which is what the tool
assembles on every turn, and the detection that resolves it. Then the routing rule
that keeps knowledge out of your working directory, followed by the two background
loops — synthesis and decay — that stop the store growing into noise. Integrations,
the security model, and the remaining open questions close it out.

A note on the `[VERIFY]` markers you will see below. They were written during design
to flag assumptions that had not been confirmed, on the principle that getting one of
them wrong would invalidate part of the design. Most have since been settled by the
implementation, and each is now annotated with how it resolved. The few that remain
open are listed together in the final section.

---

## 1. Core constraints

Four constraints shape every decision in this document, and none of them is
negotiable.

**A single interface.** Everything runs through Claude Code. There is no separate
application and no browser dependency, which keeps the whole system terminal-adjacent
and local-file-native, and means there is exactly one place to look when something
misbehaves.

**Local-first, with nothing sensitive web-facing.** Content lives on the device and is
backed up to iCloud. Only code reaches GitHub. This is a client-confidentiality
boundary rather than a preference, and it is why several features here are shaped
more awkwardly than they would otherwise be.

**One consistent operating style.** The core protocol is a single file loaded in full
on every session. There is no per-project tone, because the operator is one person
who works one way.

**Clean context separation.** Projects, and the nested sub-contexts within a project,
must not bleed into one another. Almost every failure mode this design guards against
is a variant of two contexts mixing.

## 2. The three-layer load

Every session assembles its context in a fixed order:

```
[ 1. CORE PROTOCOL ]   core.md — always, in full, project-agnostic
        +
[ 2. PROJECT CONTEXT ] resolved by detection → ~/Knowledge/{project}/{layer}/
        +
[ 3. CONVERSATION ]    the live turn: question, pasted notes, calendar screenshot
```

Layer one never changes. Layers two and three are resolved per task. The rest of this
section takes each in turn.

### 2.1 Core protocol

`core.md` is one consolidated file, loaded whole, every time. This was debated and
settled deliberately.

The technical argument is that the router does one fetch, one parse, and one
injection, with no assembly logic to get wrong. The substantive argument is that the
content — voice, decision principles, information-handling style, agent-spawn rules —
is interdependent enough that loading it selectively would risk incoherent partial
states, where the tone rules arrive without the principles that justify them. The
token cost of loading the whole file is negligible against the value of guaranteed
consistency, and the operator explicitly wants the entire operating system present on
every call rather than conditionally assembled.

It lives at `~/.core/core.md`, with supporting capability files alongside it in
`~/.core/capability/`. That supporting knowledge — frameworks, methods, command
templates, SOPs — is always available and is emphatically **not** a project; it never
resolves as one. The whole directory is iCloud-synced and never committed.

### 2.2 Project context

Project knowledge lives in the knowledge base, one top-level folder per project:

```
~/Knowledge/
  {project-a}/
    _project.md         project-level meta shared across layers
    {layer-1}/          e.g. meetings
    {layer-2}/          e.g. delivery
    {layer-3}/          e.g. a separate initiative inside the same account
  {project-b}/
  README.md             what this folder is, in twenty lines
  .gigabite/            every moving part, hidden and never browsed:
                          index/       the search index
                          imports/     raw machine-readable exports (see §7)
                          archive/     decayed context (see §6)
                          proposals/   gated synthesis output (see §5)
                          originals/   retired imports and pre-migration copies,
                                       kept so a move can be undone; indexed by nothing
                          aliases.json name variants that map onto a project
```

A project can hold several nested **layers** that load independently, so a task may
pull one layer, several, or the project meta plus one layer, as detection decides.
Layer naming is deliberately left open: it is data, decided per project, not fixed by
the tool. The rule is now as short as it can be: **every top-level folder is a
project, and the machinery is in `.gigabite/`**. An earlier layout reserved names
beginning with `_`, which meant six things had to be explained to anyone who opened
the folder and none of them were their knowledge; one hidden directory replaces the
convention entirely.

The knowledge base is a visible folder in the home directory by design. An earlier
version hid it at `~/.knowledge`, and the practical consequence was that the operator
could not find their own knowledge base in Finder — a store you cannot look at is one
you cannot verify. That is also why `~/Knowledge` is the intake surface rather than
having one: a file placed anywhere under `{project}/[{layer}/]` is indexed where it
sits by the next ingest (`ROUTING.md`), so putting something in and finding it later
are the same act. A staging folder used to sit here; it gave content two possible homes and
made "where is my meeting?" depend on whether a filing pass had run, which is a second
store wearing the first one's clothes.

Content whose project cannot be resolved is written to the knowledge **root** — a
loose, visible file indexed with an empty project, one drag from being filed. No
folder is invented for it. Guessing a project is the failure this design exists to
prevent (§3), and hiding the refusal in a folder nobody opens is barely better than
guessing.

### 2.3 Conversation context

The live turn: the current question, pasted meeting notes, a pasted calendar
screenshot, the running thread. It is ephemeral. It feeds detection and it is
synthesised at day's end (§5), but it is not itself persisted as knowledge unless
synthesis promotes it.

## 3. Context detection

On each new task the router resolves `{project, layer(s)}` in priority order, and
stops at the first thing that answers.

An **explicit marker** in the message — `@project` or `@project:layer` — wins
outright, matched case-insensitively against known projects and accepted as written if
it names one that does not exist yet. Failing that, **conversation continuity**
applies: if the thread has already established a context and the new message does not
signal a switch, stay where you are. Failing that, **keyword matching** scores the
message against the `keywords:` set held in each project's `_project.md`, and the
highest-scoring project wins. If none of those resolves it, the search runs
**unscoped** rather than guessing; a single disambiguating question is acceptable
when the ambiguity genuinely matters, but the default is to load and proceed.

Detection resolves both the project *and* the layer, because a project can contain
distinct sub-contexts that must not blend. When a project is confidently known, recall
is scoped to it and then topped up with unscoped hits, so a wrong guess narrows the
answer without hiding anything.

## 4. Working directory versus knowledge base

This is the single rule most likely to be broken by accident, so it gets its own
section.

**The trap.** Claude Code's folder picker sets a *working directory* for code. The
knowledge base is a *fixed central store*. If a file created during a session defaults
to the working directory, knowledge gets misfiled into whatever repository happens to
be selected — the wrong project — and contexts mix. That is precisely the failure the
design exists to prevent.

**The rule.** Code goes to the working directory, is version-controlled, and reaches
GitHub. Knowledge — notes, context, synthesised insight, project docs — goes to the
resolved path `~/Knowledge/{detected-project}/{layer}/`, *regardless* of the working
directory. And the knowledge base is always **read** from `~/Knowledge`, never from
the working directory.

**The mechanism.** The original design assumed Claude Code could intercept or redirect
file writes, and flagged that as load-bearing and unconfirmed. It resolved as the
documented fallback: an explicit save action rather than interception.
`gigabite/features/save.py` is the only correct way to persist knowledge, and every
path it produces is resolved under `config.KNOWLEDGE_DIR` from the detected project
and layer, with the caller's working directory never consulted. It has two writers,
because there are two kinds of content: `save_note` turns text into a markdown note,
and `save_file` copies an existing file — a screenshot, a PDF, a `.vtt` — in
byte-for-byte under the same rule. Refusing what you cannot read means losing it, so
nothing is refused. Project and layer names are sanitised into a single safe folder
name each, so `/`, `\`, and `..` cannot escape the knowledge base. This is marginally
less seamless than interception would have been and is fully functional; `ROUTING.md`
documents it in detail.

## 5. Daily synthesis

Synthesis is the feedback loop that turns a day's activity into proposed knowledge
updates. It runs on a schedule at day's end, in two halves, and only the first half is
automated.

The automated half collects and compresses. `build_digest(store, since_days=1)`
gathers the documents updated in the window, groups them by project, and keeps a short
excerpt of roughly four hundred characters per document rather than the full text.
Volume stays low on purpose, for the reasons in §6: transcripts belong in the source
tool, and the knowledge base holds distilled context. `write_proposal` then renders
that digest to `~/Knowledge/.gigabite/proposals/YYYY-MM-DD.md` with two empty approval
checklists, one for proposed knowledge updates and one for proposed changes to
`core.md`. Writing twice on the same day overwrites, so the operation is idempotent.

The second half is manual. Claude reads the digest, fills in the checklists with the
material changes — decisions made, new constraints, shifted priorities, assumptions
validated or invalidated — and the accepted items are applied by hand.

**The gate is non-negotiable.** The synthesis module contains no LLM calls and never
writes to `core.md` or to the knowledge base. Its only output is a proposal file.
Automated writes to the operating protocol without review is exactly the kind of
silent drift this whole design exists to avoid, and the split between the two halves
is what enforces it structurally rather than by good intentions.

## 6. Context volume and decay

The concern is real and arithmetic. Several meetings a day over months is hundreds of
meetings, and without a counter-pressure the active context balloons until every load
drags irrelevant history behind it.

Three mechanisms hold it down. **Compressed capture** means synthesis stores extracted
insight rather than full transcripts. **Layered loading** means a task pulls only the
relevant project and layer, never the whole store. And **reference-frequency decay**
archives what you have stopped touching.

Decay is a real mechanism rather than a sentiment. Each document's last-touch is
`accessed_utc`, falling back to `updated_utc` and then `created_utc`. Every default
search hit calls `Store.record_access`, which refreshes that timestamp. The scheduled
job finds active documents whose last-touch is older than the window and flips their
`active` flag; it deletes nothing, and documents with no parseable timestamp are left
active on the principle that we never archive what we cannot date. Archived rows stay
in the index and remain searchable on explicit request, and because `record_access`
also sets `active = 1`, re-touching an archived document restores it automatically.

The window is thirty days. The original design named fourteen as a starting point; the
implementation opened wider on purpose, because a conservative window produces fewer
surprises, and the intention is to tune down against real usage. It is a parameter on
every call, so tuning is a configuration change rather than a code change.

The net effect is that active context is a function of what you actually touch, not of
everything ever recorded. `SYNTHESIS.md` covers both loops in more depth.

## 7. External integrations

**Granola.** Content arrives because you hand it over: an export saved into a
project's `meetings/` folder, or a copied transcript through `gigabite paste`
(`/granola`), which routes it to the same place. Nothing in the codebase reads
Granola's local store — that keeps the integration free of credentials and immune to
whatever Granola changes next, which for a path used several times a day is worth more
than saving the export click. A pull from Granola's public API remains the clean
future route whenever API access is available; the parsing for it is already written.
See `GRANOLA.md`.

**Claude.ai.** Web chats have no official API, and the internal endpoints are
Cloudflare-gated against non-browser clients. The working route is an in-page export
script that runs inside the browser and produces a `conversations.json` the importer
understands, placed in `~/Knowledge/.gigabite/imports/claude_ai/`. A keychain-token
route exists and is blocked in practice. See `CLAUDE_AI.md`.

**Readable copies of machine-readable input.** An export is not content you can open,
so a document that came from one would exist in `conversations.json` and in SQLite and
nowhere a human looks — the folder would be a partial view of the store while claiming
to be all of it. `gigabite materialize` renders every indexed document as markdown
under its project (`meetings/` for meetings, `conversations/` for chats), which makes
the folder the complete picture. Each rendering carries the `doc_id:` and `source:` of
the document it renders, and `sources.notes` resolves that file to that document rather
than to a new one: without the stamp the rendering would index as a second document
with the same words and every search would return the conversation twice. It is
idempotent — the stamps on disk are the record, so it survives an index rebuild — and
non-destructive, moving raw imports into `.gigabite/originals/` rather than deleting
them.

**Calendar.** The calendar sits in a managed environment where direct AI access is not
permitted, so the input is a pasted screenshot. Reading the image is the model's job,
not the code's: in a Claude Code turn the model extracts the meetings as JSON, and the
calendar module maps each to a project, files it as a searchable document, and
attaches recalled prep for the meeting ahead. No credentials are handled and nothing
connects to the calendar system.

**External tool access.** Explicitly shelved. It was flagged as legally and
contractually fraught in the operating environment, and it is out of scope: not built,
not stubbed.

## 8. Security model

| Asset | Location | Backup | GitHub |
|---|---|---|---|
| Router and scripts (code) | working directory / repo | git | Yes |
| `core.md` and capability | `~/.core/` | iCloud | Never |
| Project knowledge and notes | `~/Knowledge/` | iCloud | Never |
| Credentials | macOS keychain | — | Never |

Content never leaves the device except to iCloud backup; nothing sensitive touches
GitHub or any web-facing surface. Credentials are held in the macOS keychain and
retrieved at runtime, never written to files or the repository — the claude.ai token
is entered through the system's own hidden prompt so that it never reaches shell
history either.

One further discipline applies at the edges. Any step that sends text off the device —
a web search, an external API call — must first check its payload for confidential
content, including project names, unreleased strategy, and internal specifics, and
strip, anonymise, or refuse. This is enforced at the point of egress rather than left
to the judgement of whatever is composing the request.

There is a gap here worth naming, because it is the one the security model does not
cover. iCloud is synchronisation, not version history. A knowledge base accumulated
over months is the most valuable and least reproducible thing on the machine, while
the code is recoverable from a clone in seconds. A real versioned backup pointed at
`~/Knowledge` is the sensible complement to everything above, and it is not something
the tool can do for you.

## 9. Build order

Phasing follows the dependency order set by the open assumptions rather than by
convenience. The two load-bearing verifications came first, because both changed the
shape of what got built. Then the core load — `core.md` always loaded, knowledge read
from `~/Knowledge`. Then context detection with nested-layer resolution, then
knowledge-write routing, then manual Granola supply and the calendar screenshot parse.
The two background loops, synthesis with its approval gate and then decay, came after
those, and the SOP system with role-based agent spawning came last, because it depends
on everything beneath it working.

## 10. Open items

| # | Item | Impact | Status |
|---|---|---|---|
| 1 | Claude Code file-write interception (§4) | Load-bearing — determines the write mechanism | **Resolved** — no interception; an explicit write through `features.save` is the mechanism |
| 2 | Granola API / programmatic export (§7) | Enhancement versus manual supply | **Resolved** — supplied by hand, no local store is read; public API pending access |
| 3 | Access-event logging for decay (§6) | Decay quality | **Resolved** — `Store.record_access` on every default search hit |
| 4 | Keychain integration (§8) | Security | **Resolved** — macOS `security`, service `gigabite:claude_ai` |
| 5 | Decay window tuning (§6) | Optimisation | **Open** — currently 30 days, tune against real usage |
| 6 | Nested-layer naming per project (§2.2) | Data, not tool | **Open by design** — decided per project |

The two items that could have materially reshaped the design have both resolved, and
in each case toward the more conservative of the two branches that were planned for.
What remains open is tuning and data modelling, neither of which requires a structural
change. The design is therefore settled; the interesting work from here is in ranking
quality and in how much of the reasoning in §5 can be made trustworthy enough to
automate without weakening the gate.
