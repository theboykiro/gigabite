# Architecture

Technical design for the Context Router. Companion to `README.md`.

This is the *how*. Where a mechanism is assumed rather than confirmed, it's marked
**[VERIFY]** — those are the things to check before or at the start of build, because
getting them wrong invalidates parts of the design.

---

## 1. Core constraints (what shapes every decision)

- **Single interface: Claude Code.** Everything runs through it — no separate app, no
  browser dependency. Terminal-adjacent, local-file-native.
- **Local-first, nothing sensitive web-facing.** Content lives on-device, iCloud for
  backup. Only code reaches GitHub.
- **One consistent operating style.** The core protocol is a single file loaded in full
  every session. No per-project tone. The operator is one person and works one way.
- **Clean context separation.** Projects (and nested sub-contexts within a project) must
  not bleed into one another.

---

## 2. The three-layer load

Every session assembles context in a fixed order:

```
[ 1. CORE PROTOCOL ]   core.md  — always, in full, project-agnostic
        +
[ 2. PROJECT CONTEXT ] resolved from detection → ~/.knowledge/{project}/{layer}/
        +
[ 3. CONVERSATION ]    live turn: question, pasted notes, calendar screenshot
```

Layer 1 never changes. Layers 2–3 are resolved per task.

### 2.1 Core protocol — single file, always loaded

Decision (settled): `core.md` is **one consolidated file**, loaded whole, every time.

Rationale, technical: the router does one fetch, one parse, one injection. The content —
voice, decision principles, information-handling style, agent-spawn rules — is
interdependent enough that selective loading would risk incoherent partial states. The
token cost of loading it whole is negligible against the value of guaranteed consistency.
This was debated; whole-file won on the grounds that the operator explicitly wants the
entire operating system present on every call, not conditionally assembled.

Supporting general-purpose knowledge (frameworks, methods, command templates) sits beside
the core as always-available capability. It is **not** a project and never resolves as one.

Location: `~/.core/core.md` (+ supporting capability files in `~/.core/`). iCloud-synced.
Never GitHub.

### 2.2 Project context — detected, nested, isolated

Structure:

```
~/.knowledge/
  {project-a}/
    {layer-1}/          e.g. delivery
    {layer-2}/          e.g. strategy / cross-team stakeholders
    {layer-3}/          e.g. a separate initiative running inside the same account
    _project.md         project-level meta shared across layers
  {project-b}/
    ...
  _historical/          decayed context (see §6)
```

A project can hold multiple **nested layers** that load independently. A task may pull
one layer, several, or the project meta plus one layer — resolved by detection (§3).
(Exact layer naming is deliberately left open; it's data, decided per project, not
fixed by the tool.)

### 2.3 Conversation context

The live turn. Ephemeral inputs — the current question, pasted Granola notes, a pasted
calendar screenshot, the running thread. Feeds detection and gets synthesised at day end
(§5) but isn't itself persisted as knowledge unless synthesis promotes it.

---

## 3. Context detection

On each new task the router resolves `{project, layer(s)}` in priority order:

1. **Explicit marker** — `@project` / `@project:layer` in the message. Wins outright.
2. **Conversation continuity** — if the thread has already established a context and the
   new message doesn't signal a switch, stay in it.
3. **Keyword match** — match message terms against per-project keyword sets held in
   `_project.md` meta.
4. **Fallback** — if genuinely ambiguous, ask a single disambiguating question. Otherwise
   never ask; load and proceed.

Detection resolves **both** which project *and* which nested layer(s), since a project
can contain distinct sub-contexts that shouldn't blend.

---

## 4. Working directory vs. knowledge base — the routing problem

**The trap.** Claude Code's dropdown sets a *working directory* for code. The knowledge
base is a *fixed central store*. If files created during a session default to the working
directory, knowledge gets misfiled into whatever repo happens to be selected — the wrong
project — and contexts mix. This is the failure the design must prevent.

**The rule.**

- **Code** → working directory (the dropdown folder). Version-controlled, GitHub.
- **Knowledge** (notes, context, synthesised insight, project docs) → resolved path
  `~/.knowledge/{detected-project}/{layer}/`, *regardless* of the working directory.
- The knowledge base is always **read** from `~/.knowledge/`, never from the working dir.

**The mechanism.** Knowledge-writes are redirected to the resolved knowledge path based on
detected context, not the working directory.

> **[VERIFY] — load-bearing.** This assumes Claude Code can intercept or redirect
> file-write operations (a hook, middleware, or equivalent). This was asserted during
> design but **not confirmed**. Check it first.
> - **If supported:** implement write-redirection so saves transparently route to
>   `~/.knowledge/{project}/{layer}/`.
> - **If not supported:** fall back to an explicit save action (a command/agent step) that
>   writes to the resolved knowledge path. Slightly less seamless, fully functional. The
>   design does not otherwise depend on interception existing.

---

## 5. Daily synthesis (scheduled feedback loop)

Runs on a schedule at day's end.

**Pipeline:**

1. **Collect** — the day's conversation threads + external inputs (pasted notes, parsed
   calendar). Reads *compressed* forms (existing note summaries), not raw transcripts, to
   keep volume down.
2. **Extract** — pull material changes: decisions made, new constraints, shifted
   priorities, validated/invalidated assumptions.
3. **Diff** — compare against current `~/.knowledge/` and `core.md`.
4. **Propose** — generate a change set: knowledge updates per project/layer, and — only
   where warranted — proposed edits to the core protocol.
5. **Gate** — nothing writes without approval. Operator reviews, accepts/rejects, then it
   commits.

The loop makes the knowledge base self-maintaining under supervision. The gate is
non-negotiable: automated writes to the operating system without review is exactly the
kind of silent drift to avoid.

---

## 6. Context volume & decay

**The concern (real):** several meetings a day over months → hundreds of meetings → the
active context balloons and every load drags irrelevant history.

**Handling:**

1. **Compressed capture** — synthesis stores extracted insight, not full transcripts.
   Transcripts stay in their source tool. Knowledge base holds distilled context only.
2. **Layered load** — a task loads only the relevant project/layer, never the whole store.
3. **Reference-frequency decay** — the actual mechanism, not a vibe:
   - Every knowledge file carries last-accessed metadata.
   - On each load/cite, update its timestamp.
   - The daily synthesis job iterates the store; any file not accessed within a decay
     window (starting point: **14 days** — tunable) is moved to `~/.knowledge/_historical/`.
   - Historical files remain **searchable on explicit request** but are excluded from
     default loads.
   - Re-access from historical restores the file to active and resets its timestamp.

Net effect: active context stays lean because it's a function of *what you actually touch*,
not of everything ever recorded.

> **[VERIFY] — minor.** Reliable last-accessed tracking needs the router to log every
> knowledge read/cite. Confirm the implementation reliably captures access events; the
> decay job is only as good as that log. Decay window (14d) is a starting value to tune
> against real usage.

---

## 7. External integrations

### 7.1 Granola (meeting notes)
- **Guaranteed path:** paste notes into the session; router detects context and files to
  the right `~/.knowledge/{project}/{layer}/`.
- **Enhancement path [VERIFY]:** whether Granola exposes a usable API/export for
  programmatic pull with the operator's own credentials is **unconfirmed**. If it exists,
  add a pull-and-route step. If not, manual paste stands. Do not block the design on it.

### 7.2 Calendar
- Managed environment, **no direct AI access permitted** → input is a **pasted screenshot**.
- Router parses the screenshot, maps meetings → project/context, pre-loads prep for the
  next/asked meeting.
- No credential handling; nothing connects to the calendar system directly.

### 7.3 External tool access (open call / actions)
- **Explicitly shelved.** Flagged as legally/contractually fraught in the operating
  environment. Out of scope for this design. Not built, not stubbed.

---

## 8. Security model

| Asset | Location | Backup | GitHub |
|---|---|---|---|
| Router / scripts (code) | working dir / repo | — | Yes |
| `core.md` + capability | `~/.core/` | iCloud | Never |
| Project knowledge / notes | `~/.knowledge/` | iCloud | Never |
| Credentials | OS keychain | — | Never |

- **Content never leaves the device** except to iCloud backup. Nothing sensitive touches
  GitHub or any web-facing surface.
- **Credentials in the OS keychain**, never in files or the repo. Retrieved at runtime.

> **[VERIFY] — security.** "OS keychain" is the standard secure pattern and the intended
> approach; confirm the exact keychain integration on the target OS at implementation.
> Do not fall back to plaintext env files or config for anything secret.

- **Web-search / external-search discipline.** Any step that sends text off-device (a
  web search, an external API call) must first check its payload for confidential content
  — project names, unreleased strategy, internal specifics — and strip/anonymise or refuse.
  Enforce at the point of egress, not left to chance.

---

## 9. Build order

Phasing is secondary right now (design is still settling), but the dependency order is
fixed by the open assumptions:

1. **Resolve the two load-bearing [VERIFY]s first** — file-write interception (§4) and
   Granola API existence (§7.1). Both change the shape of what gets built.
2. Core load: `core.md` always-loaded, plus knowledge-base read from `~/.knowledge/`.
3. Context detection (§3), including nested-layer resolution.
4. Knowledge-write routing (§4) — interception or explicit-save fallback per step 1.
5. Manual Granola paste + routing; calendar screenshot parse + prep.
6. Daily synthesis (§5) with the approval gate.
7. Reference-frequency decay job (§6).
8. SOP system + role-based agent spawning.

---

## 10. Open items summary

| # | Item | Impact | Status |
|---|---|---|---|
| 1 | Claude Code file-write interception (§4) | Load-bearing — changes write mechanism | **Verify first** |
| 2 | Granola API / programmatic export (§7.1) | Enhancement vs. manual paste | **Verify** |
| 3 | Reliable access-event logging for decay (§6) | Decay quality | Verify at impl |
| 4 | Keychain integration on target OS (§8) | Security | Confirm at impl |
| 5 | Decay window (14d) tuning (§6) | Optimisation | Tune on usage |
| 6 | Nested-layer naming per project (§2.2) | Data, not tool | Decide per project |

Items 1 and 2 are the only ones that materially reshape the initial design. The rest are
implementation details to nail down as the code goes in.
