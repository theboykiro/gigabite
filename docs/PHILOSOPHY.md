# Philosophy — a second organisational brain

This document says what gigabite is *for*. Everything else in `docs/` describes
mechanisms; this one describes the intent those mechanisms are meant to serve, and it
is the document a proposed feature has to justify itself against. It supersedes the
original specification in [`history/VISION-2026-07.md`](history/VISION-2026-07.md),
which framed the project as a personal context router.

It is deliberately client-agnostic and says "the organisation" throughout. The
concrete instantiation — which organisation, which people, which workstreams — is
**data**, and that data lives locally in `~/Knowledge` and `~/.core`. It never enters
this repository. [§6](#6-local-by-default-shared-by-decision) explains why that is a
boundary rather than a habit.

The argument runs in three movements. First, what the goal changed from and to.
Second, what that goal implies about how the system must be built — from exhaust
rather than discipline, and around rationale rather than records. Third, the
constraints that follow, and the things this is therefore not.

---

## 1. The shift

The original goal was a **context router**: one entry point that knew how *you* work
and loaded the right project knowledge, so that you stopped paying the
context-switching tax across Claude Code, Claude.ai, and standalone chats.

That was right, and it is built. But it aimed too low. A router serves one person's
working memory, and the harder, more valuable problem sits one level up:

**An organisation cannot remember why it did anything.**

The goal now is a **second organisational brain** — the institutional memory of the
organisation you work inside. Not your notes. The organisation's reasoning.

## 2. First brain, second brain

The organisation already has a first brain: Jira, Confluence, Slack, email, recorded
calls, the roadmap deck. It is not short of systems of record.

What those systems hold is **decisions as artefacts** — a ticket moved to Done, a page
last edited in March, a channel with four thousand messages. What they lose is
**decisions as reasoning**: why this and not that, who pushed back, what constraint
forced it, what was agreed to revisit, what was already tried and rejected.

That reasoning does exist. It happens in standups, refinements, prioritisation calls,
introductions, and side conversations. It is spoken aloud and then it evaporates, and
the organisation's memory of it lasts roughly as long as the tenure of the person who
was in the room. The second brain is the connective tissue the first brain
structurally cannot hold:

| The first brain knows | The second brain knows |
|---|---|
| The ticket is Done | Why it was descoped twice first |
| The page exists | That it contradicts a decision made last week |
| The meeting happened | What was actually agreed, and what stayed open |
| Who is assigned | Who to ask, and who will disagree |
| The current plan | The three plans before it, and why they died |

## 3. Built from exhaust, not from discipline

Organisational knowledge bases fail for one reason. They require someone to stop
working and go and write things down. That effort is always the first thing cut under
delivery pressure, so the wiki rots; once it is stale nobody trusts it; and once
nobody trusts it, nobody maintains it. The failure is structural, not cultural, and no
amount of encouragement fixes it.

**This system must never depend on that discipline.** It accretes from the exhaust of
work that is already happening: meeting transcripts, Claude Code sessions, claude.ai
chats, notes, calendar. The user's only obligation is to put a file where it belongs,
and even that should be rare.

The design consequence is a hard test applied to every feature: *if a feature only
works when the user is diligent, it does not work.* The system must degrade to useful
when the user is busy, out of credits, or gone for a fortnight — which is why the
capture path that matters most is a folder, indexed by plain Python, with no AI in it
anywhere. It is also why nothing handed over is ever refused: a screenshot that cannot
be read is kept and indexed by what is honestly known about it, because the
alternative is asking the user to convert it first, which is the same discipline tax
wearing a technical excuse.

## 4. Rationale over records

Retrieval is not the point. Search over everything you ever said is a commodity, and a
firehose is not memory.

What earns its place is the layer above retrieval: resolving what is **current**,
surfacing what **contradicts**, and carrying the **why** alongside the what. A decision
made in March and reversed in June must not come back as an equal citizen with the
June one. Recall that cannot rank recency and supersession is just a worse Slack
search, and worse than useless when it launders a dead decision into a confident
answer.

## 5. Org-shaped, not person-shaped

The unit of knowledge is the **organisation**, not the operator.

This is the decision that makes "personal now, shareable later" real rather than
aspirational. If the schema is shaped around one person's projects, sharing later is a
migration. If it is shaped around the organisation from day one — people, teams,
workstreams, rituals, decisions — then sharing later is only an *egress decision*, and
the structure never has to change.

So: today there is one user and everything is local. But knowledge is filed against
organisational entities, not against "my stuff".

## 6. Local by default, shared by decision

This section is non-negotiable and it constrains everything above.

Content lives on the device. The operating protocol sits in `~/.core` and
organisational knowledge in `~/Knowledge`, and neither ever enters this repository or
git. Only **code** goes to GitHub, and the repository must stay client-agnostic — no
client names, no stakeholders, no internals, including in examples.

Every stored note carries an explicit egress marker, `share: private` by default, so
that sharing is a deliberate per-item act rather than a default or a side effect. For
the same reason, client and employer knowledge are kept as separate top-level
contexts: they have different egress rules, and mixing them makes any future share
unsafe by construction. Credentials come from the macOS keychain at runtime, never
from files and never from the repository.

This is a client-confidentiality boundary, not a preference. When in doubt, the answer
is that it stays on the device.

## 7. What this is not

It is **not a wiki**, because nobody maintains it; if it needs maintaining, see §3.

It is **not RAG over everything**, because undifferentiated retrieval blends contexts
and launders stale decisions into confident answers; see §4.

It is **not a note app**. Obsidian is a store. This is a router with a judgement about
what is current.

It is **not a transcription service**. Transcripts are raw input, not the product; the
product is the reasoning extracted from them.

And it is **not a surveillance tool**. It records the organisation's reasoning, not
people's performance. If a feature would make someone reasonably uncomfortable being
recorded, it does not ship.

## 8. What this changes about the build

Concretely, against the router-era design, five things follow.

**Capture has to be obvious.** Zero-friction, zero-AI capture (§3) means somewhere a
human can actually find. The knowledge base is `~/Knowledge`, a visible folder in the
home directory rather than a hidden dotfile path, and it is the drop surface itself:
put a file under `<project>/[<layer>/]` and it is indexed where it sits. There used to
be a staging folder in front of it, and it went because two homes for the same content
means "where is my meeting?" has two answers — the version that survives is the one
where storing something and finding it later are the same act. Everything mechanical
lives behind one hidden `.gigabite/`, so what a human sees when they open the folder is
only ever their own knowledge.

**The knowledge model is org-shaped.** Top-level contexts are organisations, and layers
within one capture kinds of context — meetings, delivery, strategy — rather than
topics.

**Supersession is a first-class concern.** Recall must know what is current, not just
what matches (§4). This is the least finished part of the system and the one with the
most value left in it.

**Egress markers exist from day one**, so that sharing later never requires a
migration (§5, §6).

**Provenance is on everything.** A claim without a traceable source is not memory; it
is a guess. Every stored item records where it came from.

---

The test of this document is simple in both directions. If a proposed feature cannot
be justified from a section above, it is scope creep and should be cut. If a section
above is not shaping decisions, it is noise and should also be cut. Read it that way
rather than as a manifesto, and it stays short enough to keep being read.
