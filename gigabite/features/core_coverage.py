"""Per-slot coverage for the core setup pass (CORE_SETUP §3), model-free.

Asks one question per slot — *can the user's own history already answer this?* —
and lands each slot in `shipped`, `evidenced`, `thin` or `empty`. That per-slot
answer is what gives the setup pass a terminal condition: the pass is done when
every slot is shipped, confirmed, answered or declined, rather than when someone
judges the corpus "big enough".

No model, no network, stdlib only. The same index in gives the same coverage out.

Read-only, and that is load-bearing rather than tidy. Every query runs with
`record=False`: recording stamps `accessed_utc` and flips `active` back on, so a
recording pass would resurrect archived documents and re-date the ranking signal
it had just finished reading.

Four ways this extraction goes wrong, and what stops each here (§3):

**1. Learning the assistant's voice instead of the user's.** The corpus is mostly
assistant output by volume, so an unfiltered pass concludes the user writes in
fluent hedged paragraphs and proposes a protocol telling the assistant to sound
like an assistant. Every candidate is therefore filtered twice — the search hit's
`role` must be the user's, *and* the message the evidence is quoted from is
re-read from `messages` and checked again. Nothing that is not a user turn can
score at all, which is why an assistant-only index reports `empty`.

Role alone is not enough, though, and this is where the pass used to break. A
Claude Code session replays tool results, hook output and compaction notices
under ``role="user"``, so a file printed by the Read tool arrived here looking
exactly like a sentence the user had typed — and got quoted back to them as one.
The distinction is now recorded at ingest, where the raw transcript still shows
it (`util.ORIGIN_*`), and every candidate must be `typed`: constrained in SQL via
`origins=`, then re-checked on the message. That also fixes the opposite error,
where a `<system-reminder>` appended to genuinely typed prose used to discard the
whole turn.

**2. Mistaking speech for preference.** How someone talks to colleagues says
nothing about how they want software to behave. Meetings are excluded outright,
and the reason is sharper than "weak evidence": a meeting is indexed as one
`note`/`transcript` message with no per-speaker role, so the user's own words
cannot be separated from anyone else's. There is no user turn to filter *to*, and
using it anyway would be failure mode 1 wearing a different hat. Only the
conversational sources, where `role` is real, are read at all.

**3. Stated versus revealed preference.** People describe themselves
aspirationally, so prose style is a poor proxy for how the user wants an
assistant to act. The strongest available signal is **corrections**: the turns
where the user told an assistant to stop, be shorter, skip the summary, or just
act. Those are direct evidence about desired behaviour. `stated` slots can reach
`evidenced` only on a directive correction — never on register alone — and every
correction carries the user's own sentence in `why`, so what they confirm is a
quote of themselves rather than an assertion about themselves.

*Which way round the two signals run.* Corrections are detected across **every**
typed turn, and the slot's `evidence_terms` decide which slot a detected
correction is *about*. It used to be the other way round — terms selected the
candidate turns and the correction families ran only on what survived — which
meant a correction was found only when a configured phrase happened to appear in
the same turn. On the real corpus that left the two slots the design calls the
clear case (`tone.cut`, `tone.do`) with no evidence at all, and by CORE_SETUP §8
extraction that does not beat the shipped default has not earned its place.
Attribution therefore lives with the families, as `_DIRECTIVE_CORRECTIONS`'
third element: one correction family maps to the one or two slots it is direct
evidence *for*. Where a family genuinely speaks to two slots it is attributed to
both rather than tie-broken, because the evidence is displayed as the user's own
sentence beside each slot and approved per-slot (§5) — a wrong attribution costs
one glance and can be rejected, while a dropped one is invisible. Terms remain
the corroborating signal (`REGISTER`, and the gate on a contentless rejection
opener); they are no longer the only route in.

**4. Quoting a paste back as the user's own words.** Failure mode 2 excludes
meetings by source, but a meeting transcript, a chat log or an article pasted
*into* a typed turn is inside a message the user really did type, so neither
`role`, `origin` nor `source` can see it — and a correction-shaped sentence
spoken by somebody else then reaches `evidenced` on its own at
`STRONG_CORRECTION`, rendered as `you wrote: "…"`. Length and position are what
separate the two: a correction is short and led with, a paste is long and its
body is not where anyone puts an instruction. Hence `CORRECTION_MAX_CHARS` and
`CORRECTION_HEAD_CHARS` below, which are the only defence available here — the
turn is genuinely the user's, so there is no provenance to filter on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from .. import config, util

CoverageState = Literal["shipped", "evidenced", "thin", "empty"]

# Roles that are the user speaking. `claude_ai` normalises "human" to "user" at
# ingest, but the raw value is accepted too so a future importer that does not
# normalise cannot silently drop half the evidence.
USER_ROLES = frozenset({"user", "human"})

# Sources where `role` distinguishes speakers at all. See failure mode 2 above:
# meetings, calendar entries and notes are single-role documents, so there is no
# user turn in them to filter to.
CONVERSATIONAL_SOURCES = (config.SOURCE_CLAUDE_CODE, config.SOURCE_CLAUDE_AI)

# Signal weights. A directive correction on its own is enough to draft from; a
# turn that merely matches the slot's terms is half that, so register alone needs
# two independent documents agreeing before anything is proposed.
STRONG_CORRECTION = 2.0    # "be shorter", "skip the summary", "just do it"
WEAK_CORRECTION = 1.0      # a bare "no" / "wrong" opening a turn
REGISTER = 1.0             # the user's own turn, matching the slot's terms

EVIDENCED_SCORE = 2.0

QUOTE_CHARS = 160

# Where in a turn a correction is allowed to be, and how long a turn can be
# before it stops counting as one at all. See failure mode 4 in the docstring.
#
# Measured on the user's own typed turns: median 21 words, p90 261. A correction
# is something a person leads with, so the match has to start inside the first
# CORRECTION_HEAD_CHARS — about 100 words, which is the whole of a typical turn
# and the opening of a long one. Past CORRECTION_MAX_CHARS (~650 words, 2.5x the
# p90) a turn is a pasted document rather than an instruction, and no part of it
# is read as a correction.
CORRECTION_HEAD_CHARS = 600
CORRECTION_MAX_CHARS = 4000

# Directive corrections: the user telling an assistant to change its behaviour.
# The label is shown to the user beside the draft, so it has to read as a reason.
#
# The third element is the **attribution**: which slot(s) this family is direct
# evidence for. It lives here rather than in the registry because the mapping is
# a property of the correction, not of the question — "stop apologising" says
# something about repair whatever `tone.repair` happens to list as its terms.
#
# Fan-out is capped at the slots a family genuinely speaks to. Two is the most
# any family gets, and where two are listed the correction is attributed to both
# rather than tie-broken: it is shown as the user's own sentence beside each slot
# and approved per-slot (CORE_SETUP §5), so a wrong attribution costs a glance
# and a dropped one is silent. An id here that is not in the registry is inert.
#
# `autonomy.grid` is deliberately absent, and it is the one attribution that had
# to be argued down rather than up. "Stop asking me, just act" is a real answer —
# but to one cell of a three-cell question, and `evidenced` means *not asked*
# (CORE_SETUP §3). Attributing it there would silently skip the interview for the
# destructive and outward-facing classes, which §2 calls the cell most worth
# asking about. The same correction still lands on `decisions.momentum`, where it
# is a whole answer rather than a third of one.
_DIRECTIVE_CORRECTIONS: tuple[tuple[str, re.Pattern, tuple[str, ...]], ...] = (
    ("asked for less", re.compile(
        r"\b(too long|too verbose|too wordy|too much detail|be brief|be shorter|"
        r"much shorter|keep it short|less detail|fewer words|trim this down)\b",
        re.IGNORECASE), ("tone.length",)),
    ("rejected preamble or summary", re.compile(
        r"\b(skip the (summar\w+|preamble|intro\w*|recap)|"
        r"no (preamble|summary|recap|intro\w*|closing summary)|"
        r"(don'?t|do not|stop) (summaris\w+|summariz\w+|recap\w*|repeat\w*|restat\w+))\b",
        re.IGNORECASE), ("tone.cut", "tone.length")),
    ("rejected hedging", re.compile(
        r"\b((stop|quit) hedging|(don'?t|do not) hedge|no (caveats|hedging|disclaimers)|"
        r"(stop|quit) qualifying)\b", re.IGNORECASE), ("tone.cut", "info.uncertainty")),
    ("rejected validation or apology", re.compile(
        r"\b((stop|don'?t|do not|quit) apolog\w+|no (apology|apologies)|"
        r"(stop|don'?t) saying (sorry|you'?re right))\b",
        re.IGNORECASE), ("tone.cut", "tone.repair")),
    ("told the assistant to act, not ask", re.compile(
        r"\b(just (do it|go|act|fix it|build it|ship it)|"
        r"(stop|don'?t|do not) asking( me)?|no need to ask|"
        r"you don'?t need (to ask|my permission))\b",
        re.IGNORECASE), ("decisions.momentum",)),
    ("asked for the answer, not the working", re.compile(
        r"\b(just (the )?answer|(skip|spare me) the (working|explanation|reasoning|"
        r"derivation)|(don'?t|do not) explain)\b",
        re.IGNORECASE), ("tone.reasoning", "tone.length")),
    ("asked for pushback", re.compile(
        r"\b(push back|tell me (when )?i'?m wrong|disagree with me|"
        r"(stop|don'?t) agreeing)\b", re.IGNORECASE), ("decisions.pushback",)),
    ("told the assistant to stop something", re.compile(
        r"\bstop (doing|saying|adding|writing|telling|opening|starting)\b",
        re.IGNORECASE), ("tone.cut",)),
)

# A turn that *opens* with a flat rejection. Mid-sentence "no" is not a
# correction, so this is anchored to the start — the same distinction the routing
# register already draws for its own openers.
_REJECTION_OPENER = re.compile(
    r"^\W*(no|nope|nah|not quite|wrong|incorrect|actually)\b[\s,.!:;-]", re.IGNORECASE)


@dataclass(frozen=True)
class Correction:
    """A detected correction, and the slot(s) it is evidence for.

    `slots` empty means "attributes to nothing on its own" — see `_correction`.
    """

    weight: float
    why: str
    slots: tuple[str, ...]


@dataclass(frozen=True)
class TypedTurn:
    """One message the user actually typed, with the document it came from.

    Built once per `assess` and shared by every slot: the corpus is walked once,
    not once per configured term.
    """

    doc_id: str
    index: int
    text: str
    source: str
    title: str
    created_utc: str
    correction: Optional[Correction]


@dataclass(frozen=True)
class Evidence:
    doc_id: str
    source: str
    title: str
    created_utc: str
    snippet: str
    why: str          # which signal matched — shown to the user beside the draft


@dataclass(frozen=True)
class SlotCoverage:
    slot_id: str
    state: CoverageState
    evidence: tuple[Evidence, ...]


def _load_slots() -> tuple:
    """The registry, imported lazily so this module is importable without it."""
    from . import core_slots

    return tuple(core_slots.SLOTS)


def _quote(text: str, start: int, end: int) -> str:
    """The user's own sentence around a match, bounded to `QUOTE_CHARS`.

    Quoted verbatim on purpose: a correction shown as a paraphrase is an
    assertion about the user, and the point of the display is that it isn't one.
    """
    left = max(0, start - QUOTE_CHARS // 2)
    right = min(len(text), end + QUOTE_CHARS // 2)
    for sep in (". ", "? ", "! ", "\n"):
        cut = text.rfind(sep, left, start)
        if cut != -1:
            left = max(left, cut + len(sep))
        cut = text.find(sep, end, right)
        if cut != -1:
            right = min(right, cut + 1)
    quoted = " ".join(text[left:right].split())
    if len(quoted) > QUOTE_CHARS:
        quoted = quoted[:QUOTE_CHARS].rstrip() + "…"
    return (("…" if left > 0 else "") + quoted).strip()


def _head(text: str) -> str:
    """The opening of a turn, whitespace collapsed, bounded to `QUOTE_CHARS`."""
    flat = " ".join(text.split())
    return flat if len(flat) <= QUOTE_CHARS else flat[:QUOTE_CHARS].rstrip() + "…"


def _correction(text: str) -> Optional[Correction]:
    """How this user turn corrects an assistant, or None if it does not.

    Runs on every typed turn — no term prefilter — so `slots` is what decides
    where a hit lands. A bare rejection opener carries no `slots`: "no, that's
    wrong" says the previous answer missed, not *which* habit to change, so it
    can only corroborate a slot the turn's own terms already picked out.

    Bounded by length and position (see the constants above): a turn longer than
    `CORRECTION_MAX_CHARS` is a paste, and a match starting past
    `CORRECTION_HEAD_CHARS` is somewhere a person does not put an instruction.
    """
    if len(text) > CORRECTION_MAX_CHARS:
        return None
    # Earliest match in the turn, not first pattern in the list: the quote shown
    # to the user has to be the correction they actually led with, and pattern
    # order is an implementation detail they never see. Every family that fires
    # attributes, though — one sentence can correct two things at once, and the
    # quote shown is the same one either way.
    hits = [(m.start(), i, label, slot_ids, m)
            for i, (label, pattern, slot_ids) in enumerate(_DIRECTIVE_CORRECTIONS)
            for m in [pattern.search(text, 0, CORRECTION_HEAD_CHARS)] if m]
    if hits:
        _start, _i, label, _slot_ids, m = min(hits)
        attributed = tuple(dict.fromkeys(
            slot_id for _s, _i2, _l, slot_ids, _m in hits for slot_id in slot_ids))
        return Correction(
            weight=STRONG_CORRECTION,
            why=f'correction — {label}; you wrote: "{_quote(text, *m.span())}"',
            slots=attributed,
        )
    m = _REJECTION_OPENER.match(text)
    if m:
        return Correction(
            weight=WEAK_CORRECTION,
            why=('correction — you rejected the previous answer: '
                 f'"{_quote(text, *m.span())}"'),
            slots=(),
        )
    return None


def _user_messages(doc: dict) -> list[tuple[int, str]]:
    """(index, text) for turns the user actually typed.

    Two conditions, and the second is the one that used to be guessed at. `role`
    says which side of the conversation a message sits on; `origin` says whether
    a human typed it, recorded at ingest from the raw transcript (util.ORIGIN_*).
    Both are needed, because a Claude Code session replays tool results, hook
    output and compaction notices under ``role="user"``.
    
    This was previously inferred from markers like ``tool_use_id`` in the stored
    text, which cannot work: flattening the content blocks at ingest had already
    removed them. It threw away real turns (a system-reminder appended to typed
    prose condemned the whole message) and kept fake ones (a file printed by the
    Read tool arrived as clean prose under the user's role, and could be quoted
    back to them as their own sentence).
    """
    out = []
    for i, m in enumerate(doc.get("messages", [])):
        if (m.get("role") or "") not in USER_ROLES:
            continue
        if (m.get("origin") or "") != util.ORIGIN_TYPED:
            continue
        text = util.clean_text(m.get("text", ""))
        if not text:
            continue
        out.append((i, text))
    return out


def _typed_turns(store) -> list[TypedTurn]:
    """Every turn in the corpus the user actually typed, corrections detected.

    The corpus-wide half of the inversion. Correction detection runs here, on
    every candidate, before any slot is considered — so what a slot's terms
    decide is where a correction lands, not whether it is looked for.

    Read-only: `iter_documents` and `get_document` are plain SELECTs and neither
    stamps `accessed_utc` nor flips `active`, which `search(record=True)` would.
    Historical documents are included deliberately. Decay is a *retrieval*
    signal — how likely a document is to answer today's question — and this is
    not retrieval: the corrections a user issued a year ago are exactly as much
    evidence about how they want to be worked with as last week's.
    """
    turns: list[TypedTurn] = []
    for row in store.iter_documents():
        # Failure mode 2, applied before the document is even opened: a meeting,
        # note or calendar entry has one role for the whole document, so there
        # is no user turn in it to filter to.
        if (row.get("source") or "") not in CONVERSATIONAL_SOURCES:
            continue
        doc = store.get_document(row["doc_id"])
        if not doc:
            continue
        for index, text in _user_messages(doc):
            turns.append(TypedTurn(
                doc_id=row["doc_id"],
                index=index,
                text=text,
                source=row.get("source") or "",
                title=row.get("title") or doc.get("title") or "",
                created_utc=row.get("created_utc") or "",
                correction=_correction(text),
            ))
    return turns


def _gather(slot, turns: list[TypedTurn]) -> list[tuple]:
    """Candidate signals for one slot: (weight, created_utc, doc_id, index, ...).

    Two routes in, and the first no longer depends on the second:

    * a **correction** whose family attributes to this slot, found anywhere in
      the user's typed turns;
    * **register** — the turn matches one of the slot's `evidence_terms` —
      which corroborates, and for a `stated` slot is not evidence at all (§3).

    A contentless rejection opener sits between the two: it is a correction, but
    it names no behaviour, so it counts only where the slot's own terms show
    what was being rejected. One row per message, so a turn matching three terms
    is one piece of evidence rather than three.
    """
    terms = tuple(getattr(slot, "evidence_terms", ()) or ())
    stated = getattr(slot, "kind", "") == "stated"
    needles = [n for n in (t.strip().strip('"*').lower() for t in terms) if n]
    found: list[tuple] = []

    for turn in turns:
        lowered = turn.text.lower()
        term = next((n for n in needles if n in lowered), None)
        correction = turn.correction

        if correction and (slot.id in correction.slots
                           or (not correction.slots and term)):
            weight, why = correction.weight, correction.why
        elif term and not stated:
            # Register is style, not a statement about how the user wants an
            # assistant to behave. For a stated slot it is not evidence at any
            # volume (§3).
            weight, why = REGISTER, f'your own turn, matching "{term}"'
        else:
            continue

        found.append((
            weight, turn.created_utc, turn.doc_id, turn.index,
            Evidence(
                doc_id=turn.doc_id,
                source=turn.source,
                title=turn.title,
                created_utc=turn.created_utc,
                # The head of the typed message itself, not a search snippet: a
                # passage can merge a typed turn with the tool output that
                # followed it, and this string is shown to the user as theirs.
                snippet=_head(turn.text),
                why=why,
            ),
        ))

    # Strongest first, then most recent, then a total order on identity so the
    # same index always produces the same bundle.
    return sorted(found, key=lambda r: r[:4], reverse=True)


def _score(signals) -> tuple[float, bool]:
    """Total weight and whether a directive correction is among the signals.

    Register is counted once per document. Without that cap a single long
    session can carry a slot to `evidenced` on its own, which is one voice, not
    a pattern.
    """
    total = 0.0
    strong = False
    counted_docs: set[str] = set()
    for weight, _created, doc_id, _index, _ev in signals:
        if weight >= STRONG_CORRECTION:
            strong = True
        elif weight == REGISTER:
            if doc_id in counted_docs:
                continue
            counted_docs.add(doc_id)
        total += weight
    return total, strong


def assess(store, *, limit_per_slot: int = 5, slots=None) -> tuple[SlotCoverage, ...]:
    """Coverage for every slot in the registry. Read-only; never records access.

    `slots` overrides the registry and exists as a test seam — production callers
    pass the store and nothing else.

    Raises ``store.ReindexRequired`` on an index upgraded but not yet re-ingested,
    because every filter below rests on message provenance. Answering from an
    unpopulated column would report that the user's history says nothing about
    them, which is a plausible, wrong and unnoticeable result — precisely the
    class of failure this module exists to prevent.
    """
    if getattr(store, "provenance_pending", lambda: False)():
        from ..store import ReindexRequired

        raise ReindexRequired(
            "this index predates message provenance, so no turn can be attributed "
            "to you yet — run `gigabite ingest` (or `gigabite reindex`) first."
        )

    registry = _load_slots() if slots is None else tuple(slots)
    out: list[SlotCoverage] = []

    # One walk of the corpus for the whole pass. Every slot scores against the
    # same typed turns, so adding a slot costs no extra reads and no slot can be
    # examined against a different candidate set than its neighbours.
    turns = _typed_turns(store)

    for slot in registry:
        # Shipped slots are invariants, not preferences. No question to ask, so
        # no query to run — the pass must not touch the index for them at all.
        if getattr(slot, "kind", "") == "shipped":
            out.append(SlotCoverage(slot_id=slot.id, state="shipped", evidence=()))
            continue

        signals = _gather(slot, turns)
        if not signals:
            out.append(SlotCoverage(slot_id=slot.id, state="empty", evidence=()))
            continue

        total, strong = _score(signals)
        evidenced = total >= EVIDENCED_SCORE
        if getattr(slot, "kind", "") == "stated" and not strong:
            # A stated slot is a `[FILL]` section. Only a correction may populate
            # one; anything softer is interviewed instead of asserted.
            evidenced = False

        state: CoverageState = "evidenced" if evidenced else "thin"
        evidence = tuple(row[4] for row in signals[:max(0, limit_per_slot)])
        out.append(SlotCoverage(slot_id=slot.id, state=state, evidence=evidence))

    return tuple(out)


def summarise(coverages) -> dict:
    """Counts by state. All four keys are always present, zeros included."""
    counts = {"shipped": 0, "evidenced": 0, "thin": 0, "empty": 0}
    for coverage in coverages:
        counts[coverage.state] = counts.get(coverage.state, 0) + 1
    return counts
