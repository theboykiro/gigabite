# Roadmap — building out the autonomy layer

The design is in [`AUTONOMY.md`](AUTONOMY.md); this is the build order and the
done-conditions. Each item says what it is, what it unblocks, how you know it
works, and what would make it the wrong thing to build.

## Order of work

The numbering is the order to build in, and it follows the critical path:

```
1 ledger ──▶ 2 policy engine ──▶ 5 capability registry ──▶ 6 planner/executor
                                        └──▶ 7 egress filter ──┘
```

Nothing that touches the world can be built safely before the policy engine,
because every connector and every executor step resolves to an action class. That
is what makes 2 the next item rather than the cheapest one.

**3, 4 and 8 are off the critical path** and can be picked up at any time, by
anyone, in parallel:

- **3 (register router)** touches recall and prompt handling only. It never reaches
  the action path, so it depends on nothing above it. It is the smallest item and
  the one whose effect is felt first, which makes it the right thing to interleave
  when the critical path is blocked — not the right thing to do *instead*.
- **4 (blocker channel)** needs `depends_on` on steps, which is item 6's concern;
  the storage it uses already exists. Build it with 6 or just before.
- **8 (distribution hygiene)** runs continuously and gates handing the tool to
  anyone else, not any item here.

**Status legend:** ✅ done · ◐ in progress · ○ not started · ⇢ next

---

## 1. ✅ Ledger, audit log, kill switch

`gigabite/features/ledger.py` · `gigabite run` / `gigabite audit` ·
`tests/test_ledger.py`

Durable runs, steps, decisions, blockers, artifacts and audit in their own SQLite
file; a monotonic `version` per run with a `StaleVersion` guard; a file-based
global kill switch; and the attention accounting that item 8's KPI needs.

Everything below assumes it. Nothing below is safe or measurable without it.

---

## 2. ⇢ Policy engine

*Depends on: 1. On the critical path — 5, 6 and 7 all wait on this.*

**What.** The authority table from [`AUTONOMY.md` §4](AUTONOMY.md#4-layer-4--the-authority-table)
as executable code: a single `authorize(action_class, action, context) -> Decision`
chokepoint that every side-effecting call passes through, returning allow / require
approval / refuse, and writing an `audit` row either way.

**Why it is item 2.** A guardrail expressed in a prompt is steering, and steering
can be argued with. The tool surface is the only guardrail that holds. Building the
planner first would mean retrofitting permission checks into a call graph that
already assumes it can do anything.

**Shape.**
- Action classes as constants, matching the §4 table.
- Policy in a user-editable file (`~/.core/policy.toml` or similar), not source —
  this is per-user configuration, not a product decision.
- The `never` row is enforced in code and is not addressable from the policy file.
  A rule a user can relax is not a wall.
- Approvals batch by class per run rather than prompting per call, so "yes, open
  tickets for this run" is one answer and not eleven.

**Done when.** A call in the `third-party-create` class cannot reach the network
without either a recorded approval or a refusal in the audit trail, demonstrated
by a test that asserts the refusal — the negative case is the one that matters.

**Wrong if.** You find yourself adding a policy exception for a specific caller.
That means the action classes are cut wrong; recut them.

---

## 3. ○ Register router (spar / brief / execute)

*Depends on: nothing. Parallel — pick it up any time.*

**What.** [`AUTONOMY.md` §7](AUTONOMY.md#7-layer-6--the-register-router). A mode
resolved from cheap signals — prompt length, imperative verbs, whether a file or
link is named, inter-prompt latency, whether the last turn was a correction — that
decides how much recall is injected, whether tools are available, and how long the
answer should be.

**Why here.** Smallest item on the list, entirely self-contained, and it fixes the
most-felt problem: recall currently fires on every prompt over two words and
injects up to five snippets regardless of whether the exchange is a three-word
volley or a two-hour research task.

**Shape.**
- Mode resolution in `features/routing.py` alongside project resolution — it is the
  second routing axis, and the module is already named for the job.
- The hook consumes it: `spar` suppresses recall entirely, `brief` injects fully
  and cites, `execute` injects fully and permits tools.
- Never switch mode inside one answer. Announce only the `spar → execute`
  transition, because that is the one where the user's data starts moving.

**Done when.** A short conversational turn injects nothing and adds no measurable
latency; a research prompt injects the same recall it does today. Both asserted in
tests against fixed prompts, so the classifier can be tuned without re-measuring by
hand.

**Wrong if.** It needs a model call to decide. That puts a round-trip in front of
every prompt to save a round-trip.

---

## 4. ○ Blocker channel

*Depends on: 1 for storage, 6 for step dependencies. Build it with 6.*

**What.** [`AUTONOMY.md` §6](AUTONOMY.md#6-layer-5--the-blocker-channel). The
storage exists (item 1); this is the behaviour around it: park what cannot be got,
carry on with everything that does not depend on it, surface the parked set once at
the end.

**Shape.**
- Dependency awareness in the plan, so "does not depend on it" is derivable rather
  than guessed. This is the first item that requires steps to declare `depends_on`.
- A batched end-of-run presentation, and a `gigabite run blockers` digest across
  runs for the ones that outlive a single mission (an account, a licence, a
  decision only the user can make).
- Resolving a blocker offers to resume the steps that were waiting on it.

**Done when.** A run that hits a paywall at step 2 of 6 completes steps 3–6, ends
with status `blocked` rather than `failed`, and surfaces one actionable list.

**Wrong if.** It interrupts. The whole value is that five interruptions across an
afternoon become one five-minute list.

---

## 5. ○ Capability registry and credential broker

*Depends on: 2.*

**What.** A declared connector per integration — auth method, scopes, read/write,
action class, rate limit — and a broker that holds credentials in the macOS
keychain and hands out short-lived handles rather than values.

**Why after the policy engine.** Every connector maps onto an action class. Adding
connectors before there is a policy engine means adding capability with no way to
constrain it.

**Shape.**
- First-time auth is performed **by the user**, in the provider's own flow. The
  agent never sees, types, or stores a credential value — it asks for the
  connection and reports whether it exists.
- One connector manifest format, so adding an integration is data and not code.
- A missing connection is a **blocker**, not an error: "Jira is not connected;
  authenticate once and this step resumes."
- The existing `claude_ai_live` keychain handling is the pattern to generalise
  (value passed by stdin, never argv, never printed) — and the undocumented
  internal-API access in that same module is the pattern *not* to generalise.

**Done when.** Adding a new read-only connector requires no change to the policy
engine, the ledger, or the planner.

---

## 6. ○ Planner and executor

*Depends on: 1, 2, 5.*

**What.** The loop that turns a goal into steps, runs them, validates each against
its contract, and recovers. This is the item everything else exists to make safe.

**Shape.**
- Decompose to the ledger **before** executing. A plan that only exists after the
  fact is a log.
- One output contract per step, validated before the step counts as done. A step
  that cannot state what it will produce is not decomposed finely enough.
- The recovery ladder, in order: **retry → fallback → escalate → degrade →
  compensate.** `attempts` already accumulates in the ledger; the ladder reads it.
- Budgets are enforced, not advisory: wall time, token spend, and tool calls, each
  ending the run with `blocked` and a reason rather than silently continuing.
- Re-planning is a `decision` row with the rejected branch recorded, not a silent
  rewrite of the plan.

**Done when.** A multi-step mission survives being killed mid-flight and resumed in
a new process, with no duplicated side effects — which is the property the whole
design is for, and the one to write the test for first.

**Wrong if.** It works only when nothing fails. Build the failure paths first; the
happy path is the easy half.

---

## 7. ○ Egress filter

*Depends on: 2. Gates any open-web work, so land it before 6 runs unattended.*

**What.** A single chokepoint that every outbound request — web search, fetch,
connector call, model call to anything hosted — passes through, redacting against a
local term list before the request leaves.

**Why it is not optional.** The operating protocol says project names and internals
are stripped before any external call. Open-domain deep research is a continuous
egress event, and a prompt instruction will not hold under a hundred autonomous
searches.

**Shape.**
- Term list lives in `~/.core`, never in the repository. The repository is itself an
  egress boundary.
- **Refuse rather than degrade.** If a query cannot be sanitised while remaining
  meaningful, that is a blocker with a clear reason, not a quietly weakened search.
- Every redaction is auditable — you need to be able to answer "what actually left
  this machine" later, exactly.

**Done when.** A test asserts that a query containing a term from the list never
reaches the transport layer, and that the run records a blocker instead.

---

## 8. ◐ Distribution hygiene — runs alongside everything

Each of these is survivable while the only user is the person who wrote it. None is
survivable on someone else's laptop, where each becomes a silent failure the user
cannot diagnose and has no reason to trust.

| | Item | Why it becomes urgent when distributed |
|---|---|---|
| ○ | **Deletion detection** | Delete a file in `~/Knowledge` and its document, messages and passages stay indexed forever. `delete_document` exists but is unreachable. On someone else's machine this is "I deleted it and it still comes back." |
| ○ | **No index writes on the recall path** | `route` calls `search(record=True)`, so every prompt runs a write transaction — and merely *matching* a decayed document un-archives it, defeating decay by retrieval. |
| ○ | **No inline ingest in interactive commands** | `/gg` and `/search` run a full ingest before answering. The heaviest operation in the codebase, on the path where latency is most visible. |
| ○ | **Real logging** | Zero `logging` calls. The scheduled daily job sends all three of its stages to `/dev/null 2>&1 \|\| true`, so it cannot report a failure at all — the launchd log it writes to will always be empty. |
| ○ | **Installer robustness** | `install.sh` is 220 lines of bash embedding two Python programs that rewrite `~/.claude/settings.json`, `~/.claude/CLAUDE.md`, `~/.zshrc` and `~/.bash_profile`, with zero test coverage. Same for the 52-line recall hook, which is the primary product surface and has none either. |
| ○ | **The baked-in binary path** | The hook hard-codes the install-time path to the `gigabite` binary and `/usr/bin/python3`. Move or rename the directory and recall dies silently, permanently, with no error anywhere. |
| ○ | **Packaging + CI** | No `pyproject.toml`, no pinned dependencies, no CI. It runs on system Python 3.9 by accident rather than by decision. |
| ○ | **Config file** | ~15 tuning constants live in source: BM25 weights, the transcript penalty, the hook score threshold and snippet cap, passage target length. All of them are per-corpus and none of them is reachable without editing the package. |

---

## Deliberately not on this list

**Multi-tenancy, a server, cross-instance sync.** Separate installs do not need to
talk to each other. Keeping it local-first and single-user is what makes the whole
privacy model simple enough to be true; adding a server would trade that for a
capability nobody asked for.

**Embeddings and reranking.** BM25 over normalised passages measures well at the
current corpus size, and the retrieval quality problem was row shape rather than
ranking. Revisit when a measured eval — not an impression — shows keyword retrieval
missing. `tools/eval_recall.py` is where that argument gets settled.

**A GUI.** One interface, and it is Claude Code.
