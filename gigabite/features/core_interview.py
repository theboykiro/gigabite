"""Core setup, step 4: the retrieval half of the conversational interview.

gigabite has no model and is not getting one (docs/CORE_SETUP.md §4), so the
interview itself is a Claude Code slash command. This module is what that command
runs: a model-free pass that says **which slots are still unanswered, what to ask
about each, and what a sensible default answer would look like** — and, once the
user has approved answers one at a time, hands them to `core_proposal.apply_proposal`,
which stays the only writer of `core.md`.

**Why there is an answers record.** `render_core_md` is a pure function of the
whole answer set, so applying one more slot means re-rendering all of them. Reading
the answers back out of the rendered markdown would be guesswork. So every apply
writes `core-answers.json` beside `core.md`: slot id → the exact markdown body the
user approved, plus the slots they explicitly declined. That file is what makes
stopping halfway safe — a later run reads it, asks only about what is left, and
re-renders the earlier answers unchanged instead of starting again. Delete it and
nothing breaks; the interview simply has no memory of which lines were approved.

**Short enough to finish** (§8). Shipped slots are never asked about. The slots
that actually leave a section marked `[FILL]` are asked first and are the whole of
the required set; the tone slots ship with a working voice and are offered
afterwards as optional tuning. Every required slot carries concrete options the
user can accept by name, because "how do you want ambiguous calls made?" as an open
question is how a setup flow stops being finished.

The options here are interview scaffold, not registry data: they are phrasings
offered for approval, and nothing in this module writes one without the user having
picked it. The registry (`core_slots`) stays the single source of what a slot *is*.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .. import config
from . import core_proposal, core_slots

ANSWERS_FILENAME = "core-answers.json"

RECORD_VERSION = 1


def answers_path() -> Path:
    # Late-resolving, like everything else that touches configured paths.
    return config.CORE_DIR / ANSWERS_FILENAME


def _core_file() -> Path:
    return config.CORE_FILE


# ---------------------------------------------------------------------------
# what to offer for each slot the scaffold leaves blank
# ---------------------------------------------------------------------------

# slot id -> (label, markdown body). The first option is the one to offer first;
# it is the scaffold's own example where the scaffold has one. "Your own words"
# is always available and is not listed here.
OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "decisions.ambiguity": (
        ("make the call", "- **Lead with a recommendation, not an options survey.** When a call is\n"
                          "  ambiguous, make the call and say why — don't hand back a menu."),
        ("keep the options open", "- **Keep the option space open on an ambiguous call.** Lay out the live\n"
                                  "  options and their trade-offs, and leave the choice with me."),
    ),
    "decisions.momentum": (
        ("act", "- **Bias to momentum.** When there's enough to act, act. Don't re-litigate\n"
                "  settled decisions or re-derive established facts."),
        ("check first", "- **Check before moving on.** When the next step isn't already agreed,\n"
                        "  say what you're about to do and wait, rather than assuming."),
    ),
    "decisions.pushback": (
        ("say it immediately", "- **Push back immediately when the reasoning is off** — a feature, not\n"
                              "  friction."),
        ("raise it at the end", "- Note disagreements once, at the end of the work, rather than\n"
                                "  interrupting it."),
    ),
    "info.verification": (
        ("verify, and say which is which", "- **Verify before asserting.** Check anything checkable before stating it,\n"
                                           "  and mark plainly what is from memory rather than confirmed."),
        ("answer fast, flag the risky bits", "- Answer from what you have; stop and verify only where being wrong\n"
                                             "  would change the decision, and say when you have."),
    ),
    "info.uncertainty": (
        ("flag it once", "- **Flag low confidence once, then move on.** Verified vs. inferred is\n"
                         "  explicit; don't carry a hedge through every sentence."),
        ("say it every time", "- State confidence wherever it is less than high, even at the cost of\n"
                              "  repetition."),
    ),
    "info.done_means": (
        ("observed working", "- **\"Done\" means observed working end-to-end**, not \"written.\" Exercise the\n"
                             "  real behaviour before claiming it."),
        ("written and reviewed", "- \"Done\" means written and reviewed; running it end-to-end is a separate\n"
                                 "  step I'll ask for."),
    ),
    # Tone slots the scaffold leaves empty. Optional — they add a line rather
    # than unblock a section.
    "tone.reasoning": (
        ("answer only", "- Give the answer, not the derivation. Working is available on request;\n"
                        "  don't pre-empt the question."),
        ("show the working", "- Show the reasoning that got there, briefly, before the answer."),
    ),
    "tone.repair": (
        ("flat correction", "- After a mistake: name what was wrong and the correction, in one line.\n"
                            "  No post-mortem unless I ask for one."),
        ("explain what went wrong", "- After a mistake: say what was wrong, why it happened, and what changes\n"
                                    "  so it doesn't repeat."),
    ),
}

# The autonomy grid is not prose, so it gets levels rather than bodies. One
# question per action class, each answered with one of these.
AUTONOMY_QUESTIONS: tuple[dict, ...] = (
    {
        "action_class": "local_reversible",
        "ask": "Editing a file, running a test — local, and undoable. What should it do?",
        "suggested": "act_and_report",
    },
    {
        "action_class": "local_destructive",
        "ask": "Deleting, overwriting, discarding work you haven't committed — local, "
               "but you don't get it back. What should it do?",
        "suggested": "confirm_once_per_class",
    },
    {
        "action_class": "outward_facing",
        "ask": "Publishing, sending, spending — it leaves the machine. What should it do?",
        "suggested": "confirm_every_time",
    },
)

AUTONOMY_LEVEL_PHRASES: dict[str, str] = {
    "suggest_only": "suggest only, never act",
    "confirm_every_time": "confirm every time",
    "confirm_once_per_class": "confirm once, then treat it as standing for that class",
    "act_and_report": "act, then report",
    "act_silently": "act silently unless asked",
}


# ---------------------------------------------------------------------------
# the answers record
# ---------------------------------------------------------------------------

def load_record() -> dict:
    """The recorded answers, or an empty record. Never raises on a bad file.

    A corrupt or hand-mangled record degrades to "nothing recorded" rather than
    to an exception: the worst case is being asked a question twice, which is
    cheaper than a setup command that cannot start.
    """
    path = answers_path()
    empty = {"version": RECORD_VERSION, "answers": {}, "declined": []}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    raw_answers = data.get("answers")
    raw_declined = data.get("declined")
    answers = ({k: str(v) for k, v in raw_answers.items() if isinstance(k, str)}
               if isinstance(raw_answers, dict) else {})
    declined = ([d for d in raw_declined if isinstance(d, str)]
                if isinstance(raw_declined, list) else [])
    return {
        "version": data.get("version", RECORD_VERSION),
        "answers": answers,
        "declined": declined,
        "updated_utc": data.get("updated_utc"),
    }


def save_record(answers: Mapping[str, str], declined) -> Path:
    """Write the answers record. Touches nothing else — never `core.md`."""
    path = answers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "version": RECORD_VERSION,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "answers": dict(answers),
        "declined": sorted(set(declined or ())),
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def _normalise(value) -> str:
    """A slot body as a string. The autonomy grid arrives as an object."""
    if isinstance(value, Mapping):
        return json.dumps(dict(value), ensure_ascii=False)
    return str(value)


# ---------------------------------------------------------------------------
# the plan the slash command consumes
# ---------------------------------------------------------------------------

def _status(slot, record) -> str:
    if slot.kind == "shipped":
        return "shipped"
    if slot.id in record["answers"]:
        return "answered"
    if slot.id in record["declined"]:
        return "declined"
    return "unanswered"


def _slot_entry(slot, record) -> dict:
    status = _status(slot, record)
    entry = {
        "id": slot.id,
        "title": slot.title,
        "section": list(slot.section),
        "kind": slot.kind,
        "status": status,
        # Required means: leaving it unanswered leaves its section marked
        # `[FILL]`. Everything else has a working default already in the file.
        "required": slot.kind != "shipped" and slot.default_text is None,
        "question": slot.question,
    }
    if slot.id == "autonomy.grid":
        entry["answer_shape"] = "grid"
        entry["action_classes"] = [dict(q) for q in AUTONOMY_QUESTIONS]
        entry["levels"] = {k: AUTONOMY_LEVEL_PHRASES[k]
                           for k in core_slots.AUTONOMY_LEVELS}
    else:
        entry["answer_shape"] = "markdown"
        entry["options"] = [{"label": label, "body": body}
                            for label, body in OPTIONS.get(slot.id, ())]
    if status == "answered":
        entry["current"] = record["answers"][slot.id]
    return entry


def plan() -> dict:
    """Everything the slash command needs to run the interview, as plain data.

    Read-only: it reads `core.md` and the answers record, and writes nothing.
    """
    record = load_record()
    core_file = _core_file()
    text = core_file.read_text(encoding="utf-8", errors="replace") if core_file.exists() else ""
    slots = [_slot_entry(s, record) for s in core_slots.SLOTS]
    outstanding = [s for s in slots if s["status"] == "unanswered"]
    return {
        "core_path": str(core_file),
        "core_exists": core_file.exists(),
        "has_fill_markers": core_slots.FILL_MARKER in text,
        "answers_path": str(answers_path()),
        "has_answers_record": answers_path().exists(),
        "answered": sorted(record["answers"]),
        "declined": sorted(record["declined"]),
        "slots": slots,
        "ask_next": [s["id"] for s in outstanding if s["required"]],
        "ask_optional": [s["id"] for s in outstanding if not s["required"]],
        "remaining_required": len([s for s in outstanding if s["required"]]),
    }


# ---------------------------------------------------------------------------
# applying approved answers
# ---------------------------------------------------------------------------

def apply_answers(answers: Mapping, *, declined=(), core_path=None) -> dict:
    """Merge newly approved answers into the record and re-render `core.md`.

    *answers* is only ever the slots the user approved, one at a time, in the
    interview. Merging with the record is what makes a second run a resumption:
    the earlier answers render exactly as they did, and nothing already approved
    is asked about again.

    Writing still goes through `core_proposal.apply_proposal`, which sets the
    existing file aside first and remains the only writer of `core.md`.
    """
    record = load_record()
    merged = dict(record["answers"])
    unknown = []
    for slot_id, value in dict(answers).items():
        try:
            core_slots.get_slot(slot_id)
        except KeyError:
            unknown.append(slot_id)
            continue
        merged[slot_id] = _normalise(value)

    declined_ids = set(record["declined"]) | {d for d in declined or ()}
    declined_ids -= set(merged)

    save_record(merged, declined_ids)

    target = Path(core_path) if core_path is not None else _core_file()
    existed = target.exists()
    core_proposal.apply_proposal(merged, core_path=target)

    rendered = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    remaining = [s.id for s in core_slots.SLOTS
                 if s.kind != "shipped" and s.default_text is None
                 and s.id not in merged]
    return {
        "core_path": str(target),
        "written": bool(merged),
        "replaced_existing": existed and bool(merged),
        "answers_path": str(answers_path()),
        "answered": sorted(merged),
        "declined": sorted(declined_ids),
        "unknown_slots": unknown,
        "remaining_required": remaining,
        "has_fill_markers": core_slots.FILL_MARKER in rendered,
    }
