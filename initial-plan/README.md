# Context Router

*Working title — name TBD.*

A single, context-aware operating layer for Claude, accessed through Claude Code.
One interface. It knows how you work, detects what you're working on, loads the
right knowledge, and keeps that knowledge current without you managing it by hand.

---

## The problem this solves

Working across several projects with Claude today means living in three
disconnected places:

- **Claude projects** — good knowledge scoping, but locked to one project and one
  browser tab.
- **Claude Code** — good local file access, but its context is whatever repo you
  pointed it at.
- **Standalone chats** — no custom context at all. Vanilla model, no memory of how
  you work.

Each has its own instructions, its own tone, its own knowledge. Open a new chat and
the context is gone. Paste meeting notes and they land nowhere — no home, no routing,
no reuse. Ask "what's next" and the model has no idea what your day looks like.

The result is a constant context-switching tax: re-explaining the same operating
principles, re-establishing tone, re-loading the same background every time you move
between pieces of work.

## Why not an off-the-shelf tool

- **Note apps (Obsidian et al.)** — a store, not a router. They hold knowledge; they
  don't decide *which* knowledge a given task needs or feed it to the model.
- **Vector DB + RAG stacks** — heavy operational overhead (hosting, indexing,
  embeddings) for a single-operator setup, and generic retrieval blends contexts
  together rather than keeping them cleanly separated.
- **Claude projects** — don't span environments. Context can't follow you from the
  browser into Claude Code or the terminal.
- **Account-level custom instructions** — one global instruction set applied to
  everything. Can't distinguish one project from another; wrong by design for
  multi-project work.

None of them give the one thing that matters here: a single entry point that stays
consistent in *how* it works while shifting cleanly in *what* it knows.

## Who this is for

Built first for a single operator running multiple parallel projects with high
context load — someone who works the same way across every project (one consistent
operating style) but needs cleanly separated project knowledge.

The design is deliberately project-agnostic. Project names, domains, and stakeholder
context are data the system loads — never anything hard-coded into the tool. That
keeps it reusable later for others with the same multi-project shape of work.

---

## How it works — the three-layer stack

Every interaction loads context in three layers, in order:

**1. Core protocol** *(always loaded, project-agnostic)*
A single `core.md` file: your voice and tone, decision principles, how you engage
with information, how agents get spun up. This is the constitutional layer — it
governs *how* the system works regardless of what you're working on. Loaded in full,
every time. Stored locally, backed up to iCloud, never pushed to GitHub or anywhere
web-facing.

Supporting reusable knowledge (PM frameworks, methods, command templates) lives
alongside the core as general-purpose capability — not a separate project, just
skills the system can draw on for any task.

**2. Project context** *(detected and loaded per task)*
The specific project you're working on: its background, stakeholders, decisions,
constraints, vocabulary. Projects can have **nested context layers** — a single
project may contain distinct sub-contexts (e.g. delivery vs. strategy vs. a separate
initiative running inside the same account) that load independently.

**3. Conversation context** *(real-time)*
The live session: your current question, pasted meeting notes, a calendar screenshot,
what you've been discussing in this thread.

The core stays fixed. Layers 2 and 3 shift with what you're doing.

---

## Key features

### Context detection
On your first message the router works out which project (and which nested layer)
you're in, from: explicit markers (`@project`), the current conversation, or keywords.
If it genuinely can't tell, it asks — otherwise it just loads and proceeds.

### Knowledge base separate from working directory
The Claude Code working directory (the folder you pick in the dropdown) is for **code**.
The knowledge base lives at a **fixed central location** (`~/.knowledge/`) and is read
regardless of which folder Claude Code is pointed at. Knowledge files you create route
to the correct `~/.knowledge/{project}/{context}/` folder based on detected context —
*not* to the working directory — so context never gets misfiled into the wrong project.

> **Open assumption — must verify before build.** This routing depends on Claude Code
> being able to intercept/redirect file-write operations (hooks or equivalent). That
> capability is assumed, not confirmed. If it doesn't exist, the fallback is an explicit
> save command that writes to the resolved knowledge path. Verify first. See ARCHITECTURE.

### Granola meeting notes
Paste notes (or pull them if a supported API path exists — to be confirmed). The system
detects which project/context they belong to and files them in the right place
automatically. No manual tagging.

> **Open assumption — must verify.** Whether Granola exposes a usable API / export for
> programmatic pull is unconfirmed. Manual paste is the guaranteed path; API is an
> enhancement to validate.

### Calendar awareness
Calendar lives in a managed environment with no direct AI access, so the input is a
**pasted screenshot**. The system parses it, maps each meeting to a project/context,
and pre-loads the right background so "what's my next meeting" comes with prep already
attached.

### Daily synthesis *(scheduled)*
At end of day the system reviews the day's conversations and external notes, extracts
what changed (decisions, new constraints, shifted priorities), and **proposes** updates
to the relevant knowledge base and — where warranted — the core protocol. You approve
before anything is written back. A feedback loop, gated by you.

### Reusable SOPs
Standard operating procedures are **separate, modular, versioned** — not baked into the
core. An SOP defines how a given process runs (e.g. a build → QA → user-review agent
chain). Agents spun up for a task load the SOP relevant to their role. Mix and match
per workflow.

---

## Storage & security model

| Thing | Where it lives | Backed up | GitHub? |
|---|---|---|---|
| Code (router, scripts) | Working dir / repo | — | **Yes** |
| Core protocol (`core.md`) | `~/.core/` local | iCloud | **Never** |
| Project knowledge / notes | `~/.knowledge/` local | iCloud | **Never** |
| Credentials (Granola etc.) | System keychain | — | **Never** |

Only code goes to GitHub. All content — core protocol, project knowledge, meeting
notes — stays local, iCloud-synced, never web-facing. Credentials are held in the OS
keychain, never in files, never in the repo. (Exact keychain approach: see ARCHITECTURE
security section — chosen for being the standard secure pattern; confirm on target OS.)

---

## Status

Design phase. This document and `ARCHITECTURE.md` are the spec. Nothing is built yet.
Next step is Claude Code reading the architecture and beginning implementation, starting
with the two open assumptions above (file-write interception, Granola API) since both
are load-bearing.
