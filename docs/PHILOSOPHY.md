# Philosophy — a second organisational brain

*What gigabite is for. Supersedes the original spec in
[`history/VISION-2026-07.md`](history/VISION-2026-07.md), which framed this as a
personal context router.*

> This document is deliberately client-agnostic. It says "the organisation"
> throughout. The concrete instantiation — which organisation, which people,
> which workstreams — is **data**, and that data lives locally in `~/.knowledge/`
> and `~/.core/`. It never enters this repo. See [§6](#6-local-by-default-shared-by-decision).

---

## 1. The shift

The original goal was a **context router**: one entry point that knew how *you*
work and loaded the right project knowledge, so you stopped paying the
context-switching tax across Claude Code, Claude.ai, and standalone chats.

That was right, and it is built. But it aimed too low. A router serves one
person's working memory. The harder and more valuable problem sits one level up:

**An organisation cannot remember why it did anything.**

The goal now is a **second organisational brain** — the institutional memory of
the organisation you work inside. Not your notes. The org's reasoning.

## 2. First brain, second brain

The organisation already has a first brain: Jira, Confluence, Slack, email,
recorded calls, the roadmap deck. It is not short of systems of record.

What those systems hold is **decisions as artefacts** — a ticket moved to Done, a
page last edited in March, a channel with 4,000 messages. What they lose is
**decisions as reasoning**: why this and not that, who pushed back, what
constraint forced it, what we agreed to revisit, what we already tried and
rejected.

That reasoning does exist — it happens in standups, refinements, prioritisation
calls, intros, and side conversations. It is spoken aloud and then it evaporates.
The org's memory of it lasts about as long as the tenure of the person who was in
the room.

The second brain is the connective tissue the first brain structurally cannot
hold:

| The first brain knows | The second brain knows |
|---|---|
| The ticket is Done | Why we descoped it twice first |
| The page exists | That it contradicts a decision made last week |
| The meeting happened | What was actually agreed, and what stayed open |
| Who is assigned | Who to ask, and who will disagree |
| The current plan | The three plans before it, and why they died |

## 3. Built from exhaust, not from discipline

The reason org knowledge bases fail is that they require someone to stop working
and go write things down. That effort is always the first thing cut under
delivery pressure, so the wiki rots, and once it is stale nobody trusts it, and
once nobody trusts it nobody maintains it.

**This system must never depend on that discipline.** It accretes from the
exhaust of work already happening: meeting transcripts, Claude Code sessions,
Claude.ai chats, notes, calendar. The user's only obligation is to drop a file
in an obvious place — and even that should be rare.

Design consequence, and it is a hard test on every feature: *if a feature only
works when the user is diligent, it does not work.* The system must degrade to
useful when the user is busy, out of credits, or gone for two weeks.

## 4. Rationale over records

Retrieval is not the point. Search over everything you ever said is a
commodity — and a firehose is not memory.

What earns its place is the layer above retrieval: resolving what is **current**,
surfacing what **contradicts**, and carrying the **why** alongside the what. A
decision from March that was reversed in June must not come back as an equal
citizen with the June one. Recall that cannot rank recency and supersession is
just a worse Slack search.

## 5. Org-shaped, not person-shaped

The unit of knowledge is the **organisation**, not the operator.

This is the decision that makes "personal now, shareable later" real rather than
aspirational. If the schema is shaped around one person's projects, sharing later
is a migration. If it is shaped around the org from day one — people, teams,
workstreams, rituals, decisions — then sharing later is only an *egress
decision*, and the structure never has to change.

So: today there is one user, and everything is local. But knowledge is filed
against org entities, not against "my stuff".

## 6. Local by default, shared by decision

Non-negotiable, and it constrains everything above.

- Content lives on the device: `~/.core/` (operating protocol) and
  `~/.knowledge/` (org knowledge). Never in this repo, never in git.
- Only **code** goes to GitHub. The repo must stay client-agnostic — no client
  names, no stakeholders, no internals, including in examples.
- Every stored note carries an explicit egress marker (`share: private` by
  default). Sharing is a deliberate, per-item act, never a default and never a
  side effect.
- Client and employer knowledge are separate top-level contexts, because they
  have different egress rules. Mixing them makes any future share unsafe.
- Credentials come from the OS keychain at runtime. Never files, never the repo.

This is a client-confidentiality boundary, not a preference. When in doubt, the
answer is: it stays on the device.

## 7. What this is not

- **Not a wiki.** Nobody maintains it. If it needs maintaining, see §3.
- **Not RAG over everything.** Undifferentiated retrieval blends contexts and
  launders stale decisions into confident answers. See §4.
- **Not a note app.** Obsidian is a store; this is a router with judgment about
  what is current.
- **Not a transcription service.** Transcripts are raw input, not the product.
  The product is the reasoning extracted from them.
- **Not a surveillance tool.** It records the org's reasoning, not people's
  performance. If a feature would make someone reasonably uncomfortable being
  recorded, it does not ship.

## 8. What this changes about the build

Concretely, versus the router-era design:

1. **An obvious drop folder.** Zero-friction, zero-AI capture (§3) — a folder in
   the operating directory, not a hidden path in `~`.
2. **Org-shaped knowledge model.** Top-level contexts per organisation; layers
   for the kinds of context within one (meetings, delivery, strategy).
3. **Supersession is a first-class concern.** Recall must know what is current,
   not just what matches (§4).
4. **Egress markers from day one**, so sharing later never needs a migration (§5,
   §6).
5. **Provenance on everything.** A claim without a traceable source is not
   memory; it is a guess. Every stored item records where it came from.

---

*The test of this document: if a proposed feature can't be justified from a
section above, it's scope creep. If a section isn't shaping decisions, it's noise
— cut it.*
