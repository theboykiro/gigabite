# Core setup — deriving the protocol instead of shipping a blank one

Every install currently seeds `~/.core/core.md` from a static scaffold, and three of its
six sections are marked `[FILL]` for the user to complete by hand. Almost nobody does.
This document designs the replacement: a setup pass that treats `core.md` as a set of
questions rather than a document, works out which of them the user's own history can
already answer, interviews them for the rest, and proposes a finished protocol for
approval. The slot registry, coverage pass, proposal writer, interview and installer
step are built; the extraction half is demoted to optional corroboration, for the reason
in §8.

It sits between three things that already exist. The scaffold it replaces is
`install/scaffold/core.md`; the approval gate it reuses is the proposal mechanism in
[SYNTHESIS.md](SYNTHESIS.md); the first-run screen it extends is the welcome brief
described in [FEATURES.md](FEATURES.md). Read [PHILOSOPHY.md](PHILOSOPHY.md) first if
the question "why not just ask a model to write it" seems obvious — the answer is in
§4, and it is not obvious.

---

## 1. The problem

`core.md` is the constitutional layer: loaded in full, every session, project-agnostic.
It is the difference between an assistant that behaves like the user's and one that
behaves like everyone's. Yet the installer's promise — stated in `install/README.md` and
kept by `copy_if_absent` — is that it never touches an existing `core.md`. On a machine
that has one, that promise is right and must not change. On a machine that does not, it
means the file is created once, from a template, and then left alone forever at exactly
the moment it is least personal.

The failure this causes is quiet rather than loud. Nothing errors. The user simply gets
a competent generic assistant, concludes that the tool is a search index with extra
steps, and never discovers that the protocol layer was the point. A blank `[FILL]`
section does not announce itself as unfinished during normal use.

The second machine makes it sharper. Code comes from git; content does not. A user
installing on a second laptop gets their repo back and none of their protocol, because
`~/.core` is local by design and deliberately never pushed. They have already answered
these questions once and are asked to answer them again from scratch.

## 2. Slots

The unit of design is the **slot**: one question the protocol must answer, with a
stable id, the section it renders into, and a rule for how it can be filled. A slot is
data, not code — the set of them is a registry in the same shape as the integrations
registry, so adding a question is adding an entry rather than adding a branch.

Slots come in three kinds, and conflating them is the main way this feature goes wrong.

**Shipped invariants** are not preferences at all. Confidentiality and egress (§4) and
knowledge routing (§5) describe how the tool is safe to operate, not how the user likes
to work. "Never derive a project from the working directory" is not a taste. These ship
filled, are never asked about, and are shown once as something the user may edit rather
than something they must decide.

**Revealed slots** can be evidenced from the user's own corpus, because the corpus
records how they actually behave. Voice and tone (§1) is the clear case.

**Stated slots** are the current `[FILL]` sections — decision principles (§2), how the
assistant should engage with information (§3), and when work gets delegated (§6). The
scaffold's own note is correct that these "encode how *you* operate and can't be
inferred", with one amendment developed in §3 below: they cannot be inferred from how
the user writes, but they can sometimes be evidenced from how the user has *corrected*
an assistant.

| Slot | Section | Kind | Filled by |
|---|---|---|---|
| `tone.cut` | 1 | revealed | Corrections plus observed register |
| `tone.do` | 1 | revealed | Observed register in the user's own turns |
| `tone.length` | 1 | revealed | Explicit length corrections |
| `tone.reasoning` | 1 | revealed | Whether the user asks for the working or only the answer |
| `tone.repair` | 1 | stated | Interview, evidenced by reaction to past errors |
| `tone.invariants` | 1 | shipped | Never-fabricate rules; not negotiable by tone |
| `decisions.ambiguity` | 2 | stated | Interview |
| `decisions.momentum` | 2 | stated | Interview |
| `decisions.pushback` | 2 | stated | Interview, strongly evidenced by corrections |
| `autonomy.grid` | 2, 6 | stated | One level per action class; evidenced by confirm/act history |
| `info.verification` | 3 | stated | Interview |
| `info.uncertainty` | 3 | stated | Interview, evidenced by reaction to hedging |
| `info.citation` | 3 | shipped | Recall must be attributable; the tool depends on it |
| `info.done_means` | 3 | stated | Interview |
| `egress.*` | 4 | shipped | Invariant |
| `routing.*` | 5 | shipped | Invariant |
| `agents.verification` | 6 | shipped | A subagent's "done" is a claim, not evidence |

**Autonomy is a grid, not a principle.** Delegation is the question users answer worst in
prose, because "check with me before anything risky" sounds complete and specifies
nothing. Human-factors work has a better shape for it. Sheridan and Verplank (1978)
treat autonomy as an ordinal scale rather than a switch, and Parasuraman and Riley (1997)
show reliance is not global — the same person delegates differently by task and by how
reliable they have seen the system be. So `autonomy.grid` is answered as one level per
action class rather than an essay. The classes, by blast radius, are **local and
reversible** (edit a file, run a test), **local and destructive** (delete, overwrite,
discard uncommitted work), and **outward-facing** (publish, send, spend). The levels,
ascending, are *suggest only*, *confirm every time*, *confirm once per class and treat it
as standing*, *act and report*, and *act silently unless asked*.

Three choices replace two prose slots, and the answer becomes checkable against what the
assistant actually did — a paragraph about risk appetite is not. The grid also gives
extraction something to match: past confirm-then-act sequences in the corpus land in
specific cells. The current scaffold collapses into two of them and silently omits the
destructive class, which is the cell most worth asking about and the one prose hid.

**Two slots the section vocabulary was missing.** Lee and See (2004) argue that
appropriate reliance depends on a system exposing its own uncertainty, so that trust
tracks reliability instead of drifting above or below it. Nothing in the existing six
sections says *how* low confidence should be surfaced — §3 governs verifying before
asserting, which is a different act — so `info.uncertainty` is new. The second is
`tone.repair`: what happens after the assistant is wrong. The scaffold ships a rule for
naming the error, but the amount of ceremony a user wants around it is personal, and it
is the moment where trust is actually won or lost rather than merely spent.

Both of these are stated slots with revealed evidence available, which is the pattern
§3 describes: how someone reacted to hedging, or to a past mistake, is better evidence
than how they describe wanting to be treated.

`decisions.ambiguity` has a named construct behind it too. Need for closure (Kruglanski
and Webster, 1994) describes the variance directly: a high-closure user wants the call
made and the reasoning compressed, a low-closure user wants the option space kept open,
and giving each the other's treatment reads as evasiveness or as bulldozing. Naming the
axis matters mainly because it tells the interview what to ask — the useful question is
about discomfort with an unresolved choice, not about preferred answer length.

A caveat on all of the above, since the protocol these slots produce will tell an
assistant how to weigh evidence. The human-factors work is engineering-grounded and has
held up. The social-psychology constructs are from a literature with known replication
problems, and they are used here for vocabulary and question design rather than for any
claim about effect sizes. None of it is a validated instrument for configuring an
assistant; no such instrument exists. It buys better-shaped questions, and that is the
whole of the claim.

The table is the specification of scope. If a proposed question does not fit a slot, it
does not go in `core.md` — the scaffold's closing line, "if a rule isn't load-bearing,
it's noise", is the acceptance test, and a setup flow that grows the file is a
regression even when every added line is true.

## 3. Coverage, and the stopping condition

The question this design exists to answer is "how do we know we have enough context",
and per-slot coverage is what makes it answerable. Rather than judging the corpus as a
whole, each slot is evaluated independently against the index and lands in one of four
states: **shipped** (no question to ask), **evidenced** (the corpus supports a specific
draft), **thin** (some signal, not enough to draft), or **empty**.

Evidenced slots are not asked. They are shown, with their evidence, for confirmation or
edit. Thin and empty slots are interviewed. The pass is complete when every slot is
shipped, confirmed, answered, or explicitly declined — which is a real terminal
condition rather than a judgement call, and it is the same mechanism for a new user and
an existing one. A brand-new user simply has every non-shipped slot empty and receives
the full interview; nothing special-cases them.

This is also why both paths are always offered. A fresh install on a second laptop has
an empty index, so coverage correctly reports no evidence for everything and the
interview carries the whole load. Once that machine has months of history, re-running
the pass finds evidence where there was none. The same command does both jobs.

Extraction has three failure modes that the implementation must design against, because
each produces a plausible-looking and wrong result.

The first is **learning the assistant's voice instead of the user's**. The corpus is
mostly assistant output by volume. Search hits carry a `role`, and extraction must
filter to the user's own turns; skip that filter and the pass concludes that the user
writes in fluent, hedged, well-structured paragraphs, and writes a `core.md` instructing
the assistant to sound exactly like an assistant.

The second is **mistaking speech for preference**. Meeting transcripts record how
someone talks to colleagues, which is not a statement about how they want software to
behave. Someone diplomatic in a room may want blunt tooling. Meeting sources are
therefore weak evidence for tone slots and no evidence at all for the stated ones.

The third is **stated versus revealed preference**, which is the reason the interview is
not simply replaced by extraction. Nisbett and Wilson (1977) is the grounding here: people
have limited introspective access to their own processes and will readily supply a
plausible account of themselves that is not the operative one. Users describe themselves
aspirationally. The
strongest available signal for the stated slots is neither self-description nor prose
style but **corrections** — the turns where the user told an assistant to stop doing
something, be shorter, skip the summary, or just act. Those are direct evidence about
desired assistant behaviour rather than a proxy for it, and they are the one class of
evidence that can legitimately populate a `[FILL]` section. Where a correction supports
a draft, the draft must be shown with the correction quoted beside it, so the user is
confirming a claim about themselves rather than accepting an assertion.

## 4. Where the drafting happens

gigabite has no model of its own, and that is a deliberate property rather than a gap:
`gigabite search` works in a plain terminal, and the README is explicit that Claude Code
is not strictly required. Turning `core.md` generation into a model call inside the CLI
would make the tool depend on the thing it is meant to feed.

The split that preserves this is that **gigabite retrieves and Claude drafts**. A
model-free subcommand runs the coverage pass — read-only queries against the index, the
user's own turns, per-slot evidence bundles — and prints a structured result including
which slots are evidenced and which are empty. The interview and the drafting happen in
Claude Code, through a command that runs the coverage pass and conducts the rest as a
conversation.

This is better on its own merits, not merely more architecturally convenient. "How do
you want ambiguous calls made?" is a bad question in a terminal questionnaire and a
good one in a conversation that can follow up, offer a concrete example, and notice
when an answer contradicts the evidence. The CLI half stays useful without a model,
degrades to an honest "run this in Claude Code" when there isn't one, and remains
testable deterministically.

Read-only means read-only: the coverage pass must query with recording disabled, so
inspecting the corpus does not stamp access times or resurrect archived documents and
thereby corrupt the ranking signal it just consumed.

## 5. The write gate

Nothing generated is applied. The pass produces a **proposal**, exactly as the daily
synthesis loop already does when it suggests protocol updates, and the user approves it
explicitly before anything reaches `~/.core/core.md`. The existing protocol already
states this gate as absolute, a test already pins synthesis to it, and a setup flow that
wrote directly would be the one component permitted to overwrite the constitutional
layer — which is precisely backwards.

Approval is per-slot, not all-or-nothing, because a user who disagrees with one inferred
line should not have to reject the whole draft to fix it.

If a `core.md` already exists and the user approves a generated replacement, the old
file is set aside rather than destroyed, using the same pattern the installer already
uses when it replaces the knowledge README: move to a dated copy under the managed
originals directory, then write. The installer's promise not to touch an existing
`core.md` is unaffected, because the installer still never does this — only an explicit
user-run command can, and only after approval.

## 6. Where it hooks in

`bootstrap.sh` cannot host an interview. The documented one-liner pipes `curl` into
`bash`, stdin is the pipe rather than a terminal, and `install.sh` inherits it — which
is why the installer's only interactive step already guards on `[ -t 0 ]` and defers
with a note telling the user to run the command later. Setup follows that precedent
exactly: offered inline when a terminal is genuinely present, deferred with a printed
instruction when it is not. Anything that blocks reading from a pipe that will never
deliver a keystroke hangs the install, and `set -euo pipefail` means anything that exits
non-zero aborts it.

The reliable front door is therefore the welcome brief, which already runs as the final
install step, is re-runnable at any time, and already inspects real state rather than
asserting. It gains one check: if `core.md` still contains `[FILL]` markers, say so in
the same plain register the rest of the brief uses, and name the command. That covers
the piped-install path, which is the majority path.

The step numbering in `install.sh` is currently inconsistent — early steps say `/7` and
later ones `/8`. Adding a step forces a renumber, which is the moment to fix it.

## 7. Build order

1. The slot registry as data, with the three kinds distinguished and the shipped slots
   carrying their final text. No retrieval yet. Done when the existing scaffold can be
   rendered from the registry and matches the current file.
2. The coverage pass, model-free and read-only, with role filtering and per-slot
   evidence bundles. Done when a populated index yields evidence for tone slots and an
   empty one reports every slot empty without error.
3. The proposal writer, reusing the synthesis proposal path and gate. Done when a test
   pins that no code path writes `core.md` without explicit approval.
4. The conversational interview and per-slot approval in Claude Code. Done when a new
   user reaches a complete, personal `core.md` without hand-editing markdown.
5. Installer step and welcome check, both TTY-correct. Done when the piped one-liner
   completes without hanging and tells the user what to run next.

Steps 1–3 are useful shipped alone: they make the protocol inspectable and give the
synthesis loop a schema to propose against.

## 8. What would make this the wrong thing to build

If users who complete the interview do not end up with a materially different `core.md`
from the shipped default, the questions are not earning their place and the scaffold
should simply be improved instead. If the interview is long enough that people abandon
it, it has replaced a file nobody fills in with a flow nobody finishes, which is worse
because it also costs them time. And if inferred lines are routinely wrong but plausible
enough to be waved through, the feature is actively harmful — it would put words into
the constitutional layer that the user never meant and will not think to re-read. The
per-slot evidence display and per-slot approval exist to make that failure visible, and
if it turns out they do not, the extraction half should be dropped and the interview
kept.
