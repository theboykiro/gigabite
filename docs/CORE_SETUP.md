# Core setup — `/core-setup` and `~/.core/core.md`

`~/.core/core.md` is the operating protocol: loaded in full into every Claude Code
session, the same for every project. The installer seeds it from
`install/scaffold/core.md`: a default voice plus safety rules, with three sections
(decision principles, how to handle information, delegation) marked `[FILL]`, because
they are how *you* work and can't be guessed. `/core-setup` fills them in by asking.

---

## 1. The problem

A `[FILL]` section doesn't announce itself during normal use. Left alone, the user gets a
competent generic assistant and never finds out the protocol layer was the point. The
installer must never overwrite an existing `core.md`, so the file is created once, from
a template, at exactly the moment it is least personal. The interview is the way out.

## 2. Slots

The unit of design is the **slot**: one question the protocol must answer, with a
stable id, the section it renders into, and a rule for how it can be filled. A slot is
data, not code — the set of them is a registry in the same shape as the integrations
registry, so adding a question is adding an entry rather than adding a branch.

Slots come in three kinds, and conflating them is the main way this feature goes wrong.

**Shipped invariants** are not preferences at all. Confidentiality and egress
(core.md §4) and knowledge routing (core.md §5) describe how the tool is safe to
operate, not how the user likes to work. "Never derive a project from the working
directory" is not a taste. These ship filled, are never asked about, and are there for
the user to edit rather than decide.

**Revealed slots** are about how the user writes: voice and tone (core.md §1). They
ship with a working default and are offered as optional questions.

**Stated slots** are the `[FILL]` sections: decision principles (core.md §2), how the
assistant should engage with information (§3), and when work gets delegated (§6). The
scaffold's note is right that these "encode how *you* operate and can't be inferred",
so the interview asks.

| Slot | core.md section | Kind | How it is filled |
|---|---|---|---|
| `tone.cut` | 1 | revealed | Default; optional question |
| `tone.do` | 1 | revealed | Default; optional question |
| `tone.length` | 1 | revealed | Default; optional question |
| `tone.reasoning` | 1 | revealed | Default; optional question |
| `tone.repair` | 1 | stated | Optional question |
| `tone.invariants` | 1 | shipped | Ships filled |
| `decisions.ambiguity` | 2 | stated | Interview |
| `decisions.momentum` | 2 | stated | Interview |
| `decisions.pushback` | 2 | stated | Interview |
| `autonomy.grid` | 2, 6 | stated | Interview |
| `info.verification` | 3 | stated | Interview |
| `info.uncertainty` | 3 | stated | Interview |
| `info.citation` | 3 | shipped | Ships filled |
| `info.done_means` | 3 | stated | Interview |
| `egress.*` | 4 | shipped | Ships filled |
| `routing.*` | 5 | shipped | Ships filled |
| `agents.verification` | 6 | shipped | Ships filled |

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
assistant actually did — a paragraph about risk appetite is not. It also asks about the
destructive class explicitly, the cell most worth asking about and the one prose hides.

**Two slots the section vocabulary was missing.** Lee and See (2004) argue that
appropriate reliance depends on a system exposing its own uncertainty, so that trust
tracks reliability instead of drifting above or below it. Nothing in the existing six
sections says *how* low confidence should be surfaced — core.md §3 governs verifying before
asserting, which is a different act — so `info.uncertainty` is new. The second is
`tone.repair`: what happens after the assistant is wrong. The scaffold ships a rule for
naming the error, but the amount of ceremony a user wants around it is personal, and it
is the moment where trust is actually won or lost rather than merely spent.

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

## 3. Editing a file the user has touched

The user is told to edit `core.md` freely, so `/core-setup` edits it rather than
regenerating it. An approved answer replaces only the numbered section(s) its slot
renders into (`## 2. Decision principles`, and so on). Everything else stays byte for
byte: the header, the other sections, and any section the user added. The unit is the
section because that is what the file marks. Slot bodies aren't delimited inside it,
so a hand edit *inside* a section being answered is replaced. The previous file is
always kept (§5).

When no numbered heading is left marked `[FILL]`, the scaffold's "sections marked
[FILL]" note is swapped for the "every section is filled" one, but only if it is still
there word for word.

If a section to be replaced can't be found (its heading is missing or appears twice),
the file is rendered whole from the answers, and `gigabite core apply` says so and says
where the previous version was kept. Guessing where a section starts is how a hand edit
gets lost silently.

## 4. Where the drafting happens

gigabite has no model of its own, and that is a deliberate property rather than a gap:
`gigabite search` works in a plain terminal without one. Turning `core.md` generation into a model call inside the CLI
would make the tool depend on the thing it is meant to feed.

The split that preserves this is that **gigabite stores and Claude asks**. A model-free
subcommand (`gigabite core interview --json`) prints which slots are still open; the
interview and the drafting happen in Claude Code, in `/core-setup`, and the approved
answers go back through `gigabite core apply`.

This is better on its own merits, not merely more architecturally convenient. "How do
you want ambiguous calls made?" is a bad question in a terminal questionnaire and a
good one in a conversation that can follow up, offer a concrete example, and notice
when an answer contradicts the evidence. The CLI half stays useful without a model,
degrades to an honest "run this in Claude Code" when there isn't one, and remains
testable deterministically.

## 5. The write gate

Nothing generated is applied. The user approves each answer before anything reaches
`~/.core/core.md`. Approval is per slot, not all-or-nothing, so disagreeing with one line
doesn't mean rejecting the rest. `core_proposal.apply_proposal` is the only code that
writes the file, and a test pins that.

Before every write, the existing file is moved to a dated copy under
`~/Knowledge/.gigabite/originals/`. There is no flag to skip this. The installer never
touches an existing `core.md`; only `/core-setup`, run by the user, can change it.

## 6. Where it hooks in

The documented install pipes `curl` into `bash`, so nothing interactive can run during
it. The installer offers setup only when a terminal is really present, and otherwise
prints the command to run. The reliable front door is the welcome brief at the end of
the install, which is re-runnable with `gigabite welcome`: while `core.md` still has
`[FILL]` sections, it names `/core-setup`.
