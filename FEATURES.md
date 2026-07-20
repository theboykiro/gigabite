# Context Router — Feature Overview

*Working title — name TBD.*

## Who it's for

A single operator running multiple parallel projects with high context load — someone
who works the same way across every project (one consistent operating style) but needs
cleanly separated project knowledge. Project-agnostic by design: project names, domains,
and context are data the system loads, never anything hard-coded.

## How it works

Everything runs through **Claude Code** — one interface. On each task it loads context in
three layers:

1. **Core protocol** — always loaded, project-agnostic. *How you work.*
2. **Project context** — detected per task, cleanly isolated. *What you're working on.*
3. **Conversation context** — the live turn. *The question in front of you.*

The core stays fixed; layers 2–3 shift with what you're doing. Content lives local
(iCloud backup); only code reaches GitHub.

---

## Features

### Core & context

**1. Single interface** — everything runs through Claude Code. One entry point, no
browser/app switching.

**2. Three-layer context stack** — core protocol (always loaded) → project context
(detected per task) → conversation context (live turn).

**3. Core protocol** — single `core.md`, loaded whole every session. Your operating
style, decision principles, tone. Consistent across all projects.

**4. Context detection** — resolves which project *and* which nested layer from explicit
markers, conversation continuity, or keywords. Asks only if genuinely ambiguous.

**5. Nested context layers** — a single project can hold distinct sub-contexts (e.g.
delivery vs. strategy vs. a separate initiative inside the same account) that load
independently.

### Knowledge & inputs

**6. Knowledge base separate from working directory** — code goes to the Claude Code
working folder; knowledge routes to a fixed central store based on detected context, so
nothing gets misfiled into the wrong project.

**7. Granola meeting notes ingestion** — paste (guaranteed) or API pull (unconfirmed);
auto-detects context and files notes to the right place.

**8. Calendar awareness** — pasted screenshot parsed to map meetings → context, pre-loads
prep for the next meeting.

### Maintenance & automation

**9. Daily synthesis** — scheduled end-of-day review of conversations + notes, extracts
what changed, proposes updates to knowledge base and core protocol, gated by your
approval.

**10. Reusable SOPs** — modular, versioned operating procedures loaded by role; drives
agent chains (e.g. build → QA → user-review).

**11. Reference-frequency decay** — files not accessed in a set window (14d start) move
to historical, stay searchable, restore on re-access. Keeps active context lean.

### Security

**12. Security model** — content local + iCloud only, code-only to GitHub, credentials in
OS keychain, egress-check before any off-device search.

---

## Shelved
**External tool access / open call** — flagged legally fraught in the operating environment. Out of scope.
