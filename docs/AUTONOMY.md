# Autonomy — from retrieval layer to operating partner

This document specifies the layer gigabite does not yet have: the machinery that lets
it run a long chain of work without asking, and shut up when the exchange is a short
one. It is a design, not a description — nothing below is implemented yet, and each
section says what has to exist and why.

It sits above [`ARCHITECTURE.md`](ARCHITECTURE.md), which describes the memory the
system already has, and answers to [`PHILOSOPHY.md`](PHILOSOPHY.md), which says what
that memory is for. It is client-agnostic for the same reason every other document
here is: the repository is an egress boundary, and the concrete instantiation is data.

---

## 1. What the target actually is

The tasks this has to survive are open-domain and unrepeatable: a market scan one
week, a vendor negotiation the next, a launch plan for a market nobody has worked in
before. That rules out the obvious architecture. You cannot pre-author a workflow per
task type, because the set of task types is not enumerable and never stabilises.

So the system has to be able to *construct* its own procedure, gather what it is
missing — including from the open web — and, when it hits something it genuinely
cannot get past, say so precisely rather than inventing a way around it. A paywall is
not a failure. Pretending the paywalled thing said something is.

Two consequences shape everything downstream:

**The unit of work is a mission, not a turn.** A mission has a goal, a definition of
done, constraints, a budget, and an authority level. It survives process exit, laptop
sleep, and the end of a conversation. Today nothing in gigabite survives a process —
every command is one process, start to exit, and the only durable state outside
content is a per-file `mtime:size` signature.

**Autonomy is earned per action class, not granted globally.** A system that asks
before everything is useless; one that asks before nothing is unsafe. The resolution
is a policy table plus a promotion mechanism, both described below.

---

## 2. The six layers

| # | Layer | State today |
|---|---|---|
| 0 | **Memory** — index, passages, recall | Exists. Needs mode-awareness, deletion detection, and no writes on the read path. |
| 1 | **Ledger** — runs, steps, decisions, blockers, artifacts | Missing. Foundational. |
| 2 | **Planner / executor** — decompose, contract, validate, recover | Missing. |
| 3 | **Capability registry** — connectors, scopes, credential broker | Missing. |
| 4 | **Policy engine** — the authority table, in code | Missing. |
| 5 | **Blocker channel** — park, continue, batch, escalate | Missing. |
| 6 | **Register router** — spar / execute / brief | Missing. |

Layer 1 is first because nothing above it is either safe or measurable without it.

---

## 3. Layer 1 — the ledger

**Status: built.** `gigabite/features/ledger.py`, surfaced as `gigabite run` and
`gigabite audit`, covered by `tests/test_ledger.py`.

Six tables in their own SQLite file, `~/Knowledge/.gigabite/index/ledger.db`.

```
runs      (run_id, goal, done_definition, constraints_json, budget_json,
           authority, status, project, baseline_minutes, human_touch_seconds,
           started_utc, ended_utc, halt_reason, version)
steps     (run_id, seq, kind, summary, input_json, contract_json, output_json,
           status, attempts, error, started_utc, ended_utc)
decisions (run_id, seq, step_seq, question, chosen, rejected_json, why, ts_utc)
blockers  (blocker_id, run_id, step_seq, kind, description,
           what_would_unblock, status, surfaced_utc, resolved_utc)
artifacts (artifact_id, run_id, ref, kind, created_utc)
audit     (audit_id, run_id, ts_utc, action_class, action, disposition,
           detail_json)
```

It is a **separate database from the index**, not a set of extra tables in it.
`reindex` deletes the index file and rebuilds it from the files on disk, which is
safe precisely because the index is derived; a run history is a primary record
that nothing can regenerate, so it does not live anywhere `reindex` can reach.

Three properties are load-bearing.

**`version` is monotonic per run.** Anything that reads run state records the version
it read and refuses to act on a stale one. This is the cheapest defence against the
stale-context bug, and it is the only reason a resumed or parallel step is safe.

**`decisions.rejected_json` and `decisions.why` are mandatory, not optional.** A run
that only records what it did is not auditable. The rejected branch is the part a
human needs in order to disagree with it.

**State is centralised.** One writer discipline, one place to look. Distributed state
across agents is not on the table until measured cross-agent latency is genuinely the
bottleneck, which for a single-user local tool it will not be.

The ledger is also what makes the primary KPI honest — see §8.

The kill switch lives here too, as `~/Knowledge/.gigabite/STOP`. A file rather than a
row, so that it can be set with `touch`, read while the database is locked, cleared by
hand, and seen by a human trying to work out why nothing is running. Every mutating
ledger call checks it and raises; `halt_run` deliberately does not, because halting has
to work when the switch is already engaged. Releasing it does not restart anything —
halted runs are resumed one at a time, on purpose, because a switch that automatically
un-halts everything it halted is a pause button, not a safety mechanism.

---

## 4. Layer 4 — the authority table

**Status: built.** `gigabite/features/policy.py`, surfaced as `gigabite policy`,
covered by `tests/test_policy.py`. Callers arrive with the capability registry.

Enforced in code, at the tool boundary. Not in a prompt. A prompt is steering; the
tool surface is the guardrail, and a guardrail that can be argued with is not one.

| Class | Examples | Autonomy | Gate |
|---|---|---|---|
| Read | own corpus, web, connector reads | full | none |
| Local write | notes, drafts, scratch files, knowledge base | full | logged, reversible |
| Code — working | edit, run tests, commit to a working branch | full | branch-scoped |
| Code — publishing | push to a shared remote, open/merge a PR, deploy, touch CI or secrets | supervised | confirm once per class per session |
| Third-party create | ticket, wiki page, calendar entry | supervised | explicit per-item sign-off |
| Outbound comms | email, chat, anything addressed to a person | supervised | per-item, full text shown |
| Spend | purchases, paid tiers, campaign budget | supervised | per-item, amount stated |
| Credentials | first-time auth to any connector | user-performed | the agent never handles the secret value |
| Infrastructure & security on third-party systems | ports, firewall, IAM, access control, anything that changes a security posture | **never** | hard block, no override path |

Two seams in this table deserve to be argued with rather than inherited.

**"Code changes are always permitted" needs an edge, and the edge is the repository.**
A change on a working branch is reversible by anyone at any time, so it should never
prompt. A push to a shared remote, a merge, a deploy, or an edit to CI configuration
or secrets is outward-facing and effectively irreversible, so it should. The table
draws the line there; moving it is a decision, not a default.

**Open-web research is an egress event.** The system's own protocol says nothing
sensitive leaves the device and that project names and internals are stripped before
any external call. Unbounded deep research collides with that directly, and a prompt
instruction will not hold. The filter belongs at the point of the outbound call: a
single chokepoint every web and connector request passes through, which redacts
against a local term list and refuses rather than degrades when it cannot.

Alongside the table: an **audit log** of every gated action and its disposition, a
**kill switch** that halts a run mid-flight and leaves the ledger consistent, and
**rate limits** per connector.

---

## 5. Graduated autonomy

Every new action class starts at `supervised`. It promotes to `full` on evidence and
demotes on incident, and the counters live in the ledger.

- **Promote** when a class has ≥ N approvals, zero reversals and zero output
  corrections, sustained over ≥ M days.
- **Demote immediately** on any reversal, any correction of the produced artifact, or
  a blocker rate above the class threshold.
- **Never promote** anything in the `never` row. It is not a slow lane; it is a wall.

This is the mechanism that gets the system from asking constantly to running overnight
without requiring a leap of faith at any single point. It also produces the number
that justifies each promotion, which is the same number the KPI needs.

---

## 6. Layer 5 — the blocker channel

The rule the system has to obey when it hits something it cannot get: **park it,
finish everything that does not depend on it, and present the parked set once, at the
end.** Not a dead end, and not an interruption either.

A blocker record carries what was wanted, why it could not be got, and — the field
that makes it useful — **what would unblock it**: an account, a purchase, a
credential, a human decision, a document only the user can reach. Batched at the end
of a run, that list is a five-minute task for the user instead of five separate
interruptions spread across an afternoon.

The anti-patterns this is built against are named ones: the infinite loop, premature
escalation, hidden escalation, and the black hole where work is handed off and never
comes back.

---

## 7. Layer 6 — the register router

gigabite already calls itself a router, but it routes only *project*. Mode is the
second axis, and adding it is the cheapest visible improvement in the product.

Today the recall hook fires on every prompt over two words and injects up to five
snippets — roughly 1.5 KB — regardless of what kind of exchange is happening. On a
three-word sparring turn the injected context outweighs the turn itself, and it costs
a few hundred milliseconds of serial subprocesses to do it.

Three modes, resolved from cheap signals with no model call: prompt length, question
marks and imperative verbs, whether a file, path or link is present, inter-prompt
latency, and whether the previous turn was a correction.

| Mode | Looks like | Recall | Tools | Answer shape |
|---|---|---|---|---|
| **spar** | short, fast, no artifact named | suppressed, or one line at most | none | ≤ 5 lines, no headings |
| **brief** | a question about past work or current state | full, cited | read-only | as long as the evidence needs |
| **execute** | imperative + a target or artifact | full | per policy table | plan first, then the work |

Two rules keep it from becoming annoying. Never switch mode inside a single answer.
And announce the switch only in the one direction that changes what happens to the
user's data — spar into execute — because a silent slide from banter into action is
the version of this that loses trust.

---

## 8. Measuring it

The stated primary KPI is hours saved, and it can only be claimed if the ledger
records three numbers per run: `baseline_minutes` (what it would have taken by hand —
the user's estimate at mission start, or the learned median for the task type once
there is history), `actual_wall_minutes`, and `human_touch_minutes` (time the user
spent inside gates and blockers).

```
hours_saved = baseline_minutes - human_touch_minutes
```

Wall time is deliberately not in that formula. If the system runs for two hours while
the user does something else, that is the point. What it costs the user is attention,
and attention is what the gates consume — which makes the cost of oversight visible
and gives each autonomy promotion a number behind it.

Secondary KPIs are per-user and user-defined: a `kpis` table holding a metric name, a
source, and a target, reported alongside the primary.

---

## 9. Sequence

Ordered by what unblocks what, not by visible progress.

1. **Ledger, audit log, kill switch.** Nothing else is safe or measurable first.
2. **Policy engine and the §4 table**, enforced at the tool boundary.
3. **Register router.** Small, self-contained, and fixes the most-felt problem today.
4. **Blocker channel.** Park-and-continue, batched surfacing.
5. **Capability registry and credential broker.** Keychain-backed, per connector,
   first-time auth performed by the user.
6. **Planner and executor.** Decomposition to the ledger before execution, a validated
   output contract per step, and the recovery ladder: retry, fallback, escalate,
   degrade, compensate.
7. **Egress filter** at the outbound chokepoint.

Running underneath all seven, and a hard prerequisite for handing the tool to anyone
else: the distribution hygiene the current build does not have. Deletion detection so
that removing a file removes the document. No index writes on the recall path. No
full ingest inline in an interactive command. Real logging, because today a failure in
the scheduled job cannot be reported at all. Packaging, pinned dependencies, and CI.

Every one of those is survivable while the only user is the person who wrote it. None
of them is survivable once it is installed on someone else's machine, because each one
becomes a silent failure that the user has no way to diagnose and no reason to trust.
