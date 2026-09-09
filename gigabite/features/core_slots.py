"""The operating protocol as a set of answerable questions (docs/CORE_SETUP.md §2).

`core.md` is the constitutional layer, and today it is seeded from a static
template whose sections 2, 3 and 6 are literally marked `[FILL]` for the user to
finish by hand. Almost nobody does, so most installs run a generic assistant and
never find out that the protocol layer was the point.

This module is the data half of the replacement: a **slot registry** in the same
shape as the integrations registry, so adding a question to the protocol is
adding an entry rather than adding a branch. A slot is one question the protocol
must answer — a stable id, the section(s) it renders into, and a rule for how it
can be filled:

    shipped   an invariant, not a preference. Ships filled, never asked about.
    revealed  evidenced from the user's own corpus (how they actually write).
    stated    can only be asked, though corrections in the corpus can draft it.

No retrieval and no model live here, by design: the coverage pass
(`core_coverage`) and the proposal writer (`core_proposal`) are separate, and
this module is a pure function from answers to markdown. It does no file I/O and
resolves no paths — the `~/.core/` and `~/Knowledge/` in the rendered prose are
*document text* describing the default layout, not locations this code reads or
writes. Anything that actually touches disk takes its paths from `config`.

**Honesty rule.** `render_core_md` never invents an answer. A slot with no answer
either falls back to the scaffold's working default (the tone slots, which ship
with a usable voice) or renders nothing and marks its section `**[FILL]**` — so an
abandoned setup pass produces a visibly unfinished file rather than a
confident-looking wrong one. `render_core_md({})` reproduces the current
`install/scaffold/core.md` — same six headings, same `[FILL]` sections, same
bodies — with one deliberate addition: `info.citation` is a shipped invariant in
the spec's §2 table but has no line in the scaffold, so §3 gains it. That one
difference is pinned in the tests rather than left to drift.

**Encoding of the `autonomy.grid` answer.** Every other slot's answer is a
markdown body. This one is not prose: it is one autonomy level per action class.
The answer is therefore a **JSON object string**, which keeps the public
signature `Mapping[str, str]` uniform for callers that store answers in one
dict::

    answers["autonomy.grid"] = '{"local_reversible": "act_and_report", ...}'

A plain `dict[str, str]` is accepted too, since an in-process caller composing the
answer has no reason to serialise it first. Keys must be `ACTION_CLASSES` and
values `AUTONOMY_LEVELS`; if any class is missing, or the value does not parse,
the slot counts as unanswered and its sections are marked `[FILL]` rather than
rendered from a half-filled grid.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Mapping

SlotKind = Literal["shipped", "revealed", "stated"]

FILL_MARKER = "**[FILL]**"


@dataclass(frozen=True)
class Slot:
    """One question the protocol must answer. Data, not behaviour."""

    id: str
    section: tuple[int, ...]
    kind: SlotKind
    title: str
    question: str = ""
    shipped_text: str = ""
    evidence_terms: tuple[str, ...] = ()
    # The scaffold's working default, used when the slot has no answer. `None`
    # means there is no default and the section is marked `[FILL]`; `""` means
    # the slot contributes nothing (the scaffold has no line for it) without
    # making the section look unfinished.
    default_text: str | None = None


# ---------------------------------------------------------------------------
# the autonomy grid (CORE_SETUP.md §2: a grid, not a principle)
# ---------------------------------------------------------------------------

# By blast radius, ascending.
ACTION_CLASSES: tuple[str, ...] = (
    "local_reversible",
    "local_destructive",
    "outward_facing",
)

# By autonomy, ascending.
AUTONOMY_LEVELS: tuple[str, ...] = (
    "suggest_only",
    "confirm_every_time",
    "confirm_once_per_class",
    "act_and_report",
    "act_silently",
)

_CLASS_PHRASES = {
    "local_reversible": "Local and reversible (editing a file, running a test)",
    "local_destructive":
        "Local but destructive (deleting, overwriting, discarding uncommitted work)",
    "outward_facing": "Outward-facing (publishing, sending, spending)",
}

_LEVEL_PHRASES = {
    "suggest_only": "suggest only, never act",
    "confirm_every_time": "confirm every time",
    "confirm_once_per_class": "confirm once per class, then treat it as standing",
    "act_and_report": "act, then report",
    "act_silently": "act silently unless asked",
}


def parse_autonomy_answer(value) -> dict[str, str] | None:
    """Normalise an `autonomy.grid` answer, or `None` if it is not usable.

    Accepts a JSON object string (the documented encoding) or a mapping. Returns
    `None` — meaning "unanswered" — for anything that does not resolve to a level
    for every action class, rather than guessing the missing cells.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, Mapping):
        return None
    grid = {}
    for cls in ACTION_CLASSES:
        level = value.get(cls)
        if not isinstance(level, str) or level not in AUTONOMY_LEVELS:
            return None
        grid[cls] = level
    return grid


def render_autonomy_grid(value, section: int = 2) -> str | None:
    """The grid as prose, or `None` if unanswered.

    The grid spans two sections. §2 carries it in full, as the reversibility
    principle; §6 carries a pointer, because a delegated agent is bound by the
    same three cells and restating them there would be the same rule twice.
    """
    grid = parse_autonomy_answer(value)
    if grid is None:
        return None
    if section == 6:
        return _wrap_bullet(
            "**A delegated agent inherits the autonomy grid in §2.** It never escalates"
            " a class on its own — the class is what is *recoverable*, not what the"
            " operation is called."
        )
    sentences = " ".join(
        f"{_CLASS_PHRASES[cls]} → {_LEVEL_PHRASES[grid[cls]]}." for cls in ACTION_CLASSES
    )
    if "confirm_once_per_class" in grid.values():
        sentences += (
            " Once a class is authorised, that authorisation is standing — and the class"
            " is what is *recoverable*, not what the operation is called: approval to"
            " discard scratch output is not approval to discard unpushed work."
        )
    return _wrap_bullet("**Match action to reversibility.** " + sentences)


def _wrap_bullet(text: str, width: int = 84) -> str:
    """A markdown bullet wrapped to the file's line length, continuations indented."""
    words = text.split()
    lines: list[str] = []
    current = "-"
    for word in words:
        indent = 2 if lines else 0
        if len(current) + 1 + len(word) > width and current not in ("-", " " * indent):
            lines.append(current)
            current = "  " + word
        else:
            current = f"{current} {word}" if current != "-" else f"- {word}"
    lines.append(current)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

SECTIONS: tuple[tuple[int, str], ...] = (
    (1, "Voice & tone"),
    (2, "Decision principles"),
    (3, "How I engage with information"),
    (4, "Confidentiality & egress"),
    (5, "Knowledge routing"),
    (6, "How agents get spun up"),
)

# What an unfinished section says. Lifted from the scaffold, so the `[FILL]`
# sections of a rendered file read exactly as they do today. Rendered *before*
# the section's slot bodies, which is where the scaffold puts them.
SECTION_FILL_TEXT: dict[int, str] = {
    2: (
        "How you want calls made when they're ambiguous. Examples to replace with your own:\n"
        "- Bias to a recommendation over an options-survey.\n"
        "- Reversible + low-cost → act and report; irreversible or outward-facing → confirm first.\n"
        "- Prefer the simplest thing that fully works; add machinery only when it earns its place."
    ),
    3: (
        "- What to verify before asserting; when to cite sources; how much uncertainty to surface.\n"
        "- What counts as \"done\" (e.g. tested end-to-end, not just written)."
    ),
    6: (
        "- When to delegate to a sub-agent vs. do it inline.\n"
        "- Which SOP a spawned agent loads for its role (SOPs are modular; see `~/.core/capability/`)."
    ),
}

SLOTS: tuple[Slot, ...] = (
    # -- 1. Voice & tone -----------------------------------------------------
    Slot(
        id="tone.cut",
        section=(1,),
        kind="revealed",
        title="What to cut",
        question="What do you want an assistant to stop doing — the openers, "
                 "padding and habits that make you wince?",
        evidence_terms=("stop saying", "don't preamble", "no summary", "cut the",
                        "you're absolutely right", "skip the intro"),
        default_text=(
            "*(Seeded from your TONE_OVERRIDE.md (docs/history/) — this is already your voice.)*\n"
            "\n"
            "**Cut**\n"
            "- No validation openers (\"you're absolutely right\", \"great question\", \"perfect\").\n"
            "- No reflexive apology. When something's wrong: name the error and the fix in one line.\n"
            "- No preamble restating the question. No closing summary repeating what was said.\n"
            "- No hedge-stacking or repeated caveats."
        ),
    ),
    Slot(
        id="tone.do",
        section=(1,),
        kind="revealed",
        title="What to do",
        question="How should an answer open, and what does a good one read like?",
        evidence_terms=("just tell me", "lead with", "answer first", "be direct",
                        "say it plainly"),
        default_text=(
            "**Do**\n"
            "- Open with the answer or the next action. First sentence carries substance.\n"
            "- Directness is respect. Say the thing plainly.\n"
            "- When wrong: state what was wrong, state the correction, move on. Once, sharp.\n"
            "- Push back immediately when the reasoning is off — that's a feature."
        ),
    ),
    Slot(
        id="tone.length",
        section=(1,),
        kind="revealed",
        title="Length",
        question="How long should an answer be by default, and when is longer earned?",
        evidence_terms=("too long", "shorter", "be brief", "one line", "tl;dr",
                        "keep it short"),
        default_text="- Match length to the ask.",
    ),
    Slot(
        id="tone.reasoning",
        section=(1,),
        kind="revealed",
        title="Reasoning shown",
        question="Do you want the working shown, or only the answer with the working "
                 "available on request?",
        evidence_terms=("show your working", "skip the reasoning", "just the answer",
                        "explain why", "don't explain"),
        default_text="",
    ),
    Slot(
        id="tone.repair",
        section=(1,),
        kind="stated",
        title="After a mistake",
        question="When the assistant gets something wrong, how much ceremony do you "
                 "want around the repair — a flat correction, or an explanation of "
                 "what went wrong?",
        evidence_terms=("that's wrong", "you got that wrong", "stop apologising",
                        "no need to apologise", "that's not what I asked"),
        default_text="",
    ),
    Slot(
        id="tone.invariants",
        section=(1,),
        kind="shipped",
        title="Never overridden by tone",
        shipped_text=(
            "**Never overridden by tone**\n"
            "- Don't fabricate. Don't claim files exist, tools ran, or capabilities are confirmed\n"
            "  when they aren't. Be direct about what *didn't* happen and what *isn't* known.\n"
            "- Flag a real landmine once, then move on."
        ),
    ),
    # -- 2. Decision principles ---------------------------------------------
    Slot(
        id="decisions.ambiguity",
        section=(2,),
        kind="stated",
        title="Ambiguous calls",
        question="When a call is genuinely ambiguous, do you want the recommendation "
                 "made for you and the reasoning compressed, or the option space kept "
                 "open? How uncomfortable is an unresolved choice?",
        evidence_terms=("just pick", "which one should I", "make the call",
                        "give me options", "what do you recommend"),
    ),
    Slot(
        id="decisions.momentum",
        section=(2,),
        kind="stated",
        title="Momentum",
        question="When there is enough to act on, should the assistant act, or check "
                 "back first? How much re-litigating of settled decisions can you stand?",
        evidence_terms=("we already decided", "stop asking", "just do it",
                        "we've been through this", "keep going"),
    ),
    Slot(
        id="decisions.pushback",
        section=(2,),
        kind="stated",
        title="Push-back",
        question="When your reasoning looks wrong to the assistant, should it say so "
                 "immediately, or go along and raise it later?",
        evidence_terms=("push back", "disagree with me", "tell me if I'm wrong",
                        "stop agreeing", "that's not right"),
    ),
    Slot(
        id="autonomy.grid",
        section=(2, 6),
        kind="stated",
        title="Autonomy by action class",
        question="One autonomy level per action class — local and reversible, local "
                 "but destructive, outward-facing. For each: suggest only, confirm "
                 "every time, confirm once per class, act and report, or act silently?",
        evidence_terms=("should I go ahead", "confirm before", "don't ask me again",
                        "just do it", "check with me first", "ask before you delete"),
    ),
    # -- 3. How I engage with information ------------------------------------
    Slot(
        id="info.verification",
        section=(3,),
        kind="stated",
        title="What to verify",
        question="What has to be checked before it is asserted, and what can be "
                 "stated from memory?",
        evidence_terms=("did you check", "verify that", "are you sure",
                        "you made that up", "where did that come from"),
    ),
    Slot(
        id="info.uncertainty",
        section=(3,),
        kind="stated",
        title="Surfacing uncertainty",
        question="When the assistant is unsure, how should it say so — flagged once "
                 "and moved past, or carried through the answer?",
        evidence_terms=("stop hedging", "how confident", "don't caveat",
                        "just say you don't know", "guessing"),
    ),
    Slot(
        id="info.citation",
        section=(3,),
        kind="shipped",
        title="Cite to verify",
        shipped_text=(
            "- **Cite to verify.** Attribute recalled claims to their source *(name · date)*\n"
            "  so they can be checked; never launder recall into unattributed assertion."
        ),
    ),
    Slot(
        id="info.done_means",
        section=(3,),
        kind="stated",
        title="What \"done\" means",
        question="What has to be true before something counts as done — written, "
                 "reviewed, or observed working end-to-end?",
        evidence_terms=("is it working", "did you test", "that's not done",
                        "end to end", "did you run it"),
    ),
    # -- 4. Confidentiality & egress ----------------------------------------
    Slot(
        id="egress.invariants",
        section=(4,),
        kind="shipped",
        title="Confidentiality & egress",
        shipped_text=(
            "- Treat project/client data as confidential. Nothing sensitive leaves the device.\n"
            "- Before any web search or external call, strip/anonymise project names and internals,\n"
            "  or refuse. Enforce at the point of egress.\n"
            "- Credentials come from the OS keychain at runtime, never from files or the repo.\n"
            "- **The repo is an egress boundary too.** Only code goes to GitHub. Client names,\n"
            "  stakeholders and internals never enter it — including in examples, comments and\n"
            "  test fixtures. Use a neutral placeholder."
        ),
    ),
    # -- 5. Knowledge routing ------------------------------------------------
    Slot(
        id="routing.invariants",
        section=(5,),
        kind="shipped",
        title="Knowledge routing",
        shipped_text=(
            "- **Never derive a project from the working directory.** A folder name is not a\n"
            "  project. Deriving one is how client knowledge gets filed under a tool's name.\n"
            "- **Ambiguous context is left unfiled, never guessed.** If the project isn't clear,\n"
            "  ask, or leave the file loose at the top of `~/Knowledge` where it is visible and\n"
            "  still searchable. A confidently misfiled note is worse than an unfiled one, and no\n"
            "  folder is ever invented to hold the uncertainty.\n"
            "- **Knowledge is written only through the tool** (`gigabite save`, `paste`, `add`),\n"
            "  so every path resolves under `~/Knowledge/` whatever the working directory.\n"
            "- **Before any bulk move, back up and check nothing else is writing.**"
        ),
    ),
    # -- 6. How agents get spun up ------------------------------------------
    Slot(
        id="agents.verification",
        section=(6,),
        kind="shipped",
        title="A subagent's \"done\" is a claim",
        shipped_text=(
            "- **A subagent's \"done\" is a claim, not evidence.** Verify delegated work against\n"
            "  the real artefact before relaying it — an agent reporting success while doing the\n"
            "  opposite is a real failure mode, not a hypothetical one."
        ),
    ),
)

_BY_ID = {slot.id: slot for slot in SLOTS}


def slots_by_section() -> dict[int, list[Slot]]:
    """Every slot, grouped by the section(s) it renders into, in registry order.

    A slot spanning two sections (`autonomy.grid`) appears under both.
    """
    grouped: dict[int, list[Slot]] = {number: [] for number, _ in SECTIONS}
    for slot in SLOTS:
        for number in slot.section:
            grouped.setdefault(number, []).append(slot)
    return grouped


def get_slot(slot_id: str) -> Slot:
    """The slot with this id. Raises `KeyError` for an unknown one."""
    return _BY_ID[slot_id]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

_HEADER = """# Core Protocol

*The constitutional layer. Loaded in full, every session, project-agnostic.*
*Local only — lives in `~/.core/`, backed up to iCloud, never pushed anywhere.*

This file governs **how** the system works regardless of what you're working on.
Project knowledge (the **what**) lives in `~/Knowledge/` and loads per task."""

_FILL_NOTE = """> Sections marked **[FILL]** are yours to complete — they encode how *you* operate
> and can't be inferred. Everything else is a working default you can edit freely."""

_COMPLETE_NOTE = """> Every section is filled. Edit any of it freely — this is a living file. The
> daily synthesis loop proposes updates here; nothing writes without you."""

_FOOTER = "*Keep this file tight. If a rule isn't load-bearing, it's noise.*"


def _body_for(slot: Slot, answers: Mapping[str, str], section: int = 0) -> str | None:
    """The markdown this slot contributes, or `None` when it is unanswered.

    `""` is a body: a slot the scaffold has no line for contributes nothing
    without making its section look unfinished.
    """
    if slot.kind == "shipped":
        return slot.shipped_text
    if slot.id == "autonomy.grid":
        if slot.id in answers:
            rendered = render_autonomy_grid(answers[slot.id], section)
            if rendered is not None:
                return rendered
        return slot.default_text
    answer = answers.get(slot.id)
    if answer is not None and str(answer).strip():
        return str(answer).rstrip()
    return slot.default_text


def _is_bullet_list_end(block: str) -> bool:
    last = block.rstrip().splitlines()[-1] if block.strip() else ""
    return last.startswith("- ") or last.startswith("  ")


def _join_blocks(blocks: list[str]) -> str:
    """Blank line between blocks, except between adjacent bullets of one list."""
    out = ""
    for block in blocks:
        if not out:
            out = block
            continue
        joiner = "\n" if _is_bullet_list_end(out) and block.startswith("- ") else "\n\n"
        out = out + joiner + block
    return out


def render_core_md(answers: Mapping[str, str]) -> str:
    """A complete `core.md` from slot answers. Never invents an unanswered slot.

    *answers* maps slot id → markdown body for the non-shipped slots; shipped
    slots always use their own text. `autonomy.grid` takes a JSON object string
    (or a mapping) of action class → autonomy level — see the module docstring.

    A slot with no answer falls back to its scaffold default; where there is no
    default, the section carries the scaffold's `[FILL]` guidance and its heading
    is marked, so an incomplete pass is visible rather than silently shipped.
    """
    grouped = slots_by_section()
    sections: list[str] = []
    any_unfilled = False

    for number, title in SECTIONS:
        blocks: list[str] = []
        unfilled = any(
            slot.kind != "shipped" and _body_for(slot, answers, number) is None
            for slot in grouped[number]
        )
        if unfilled:
            any_unfilled = True
            fill_text = SECTION_FILL_TEXT.get(number)
            if fill_text:
                blocks.append(fill_text)

        for slot in grouped[number]:
            body = _body_for(slot, answers, number)
            if body:
                blocks.append(body)

        heading = f"## {number}. {title}"
        if unfilled:
            heading += f"  {FILL_MARKER}"
        sections.append(_join_blocks([heading] + blocks) if blocks else heading)

    note = _FILL_NOTE if any_unfilled else _COMPLETE_NOTE
    parts = [_HEADER, note, "---", *sections, "---", _FOOTER]
    return "\n\n".join(parts) + "\n"
