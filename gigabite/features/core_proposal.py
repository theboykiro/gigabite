"""Core setup, step 3: the proposal writer and the gated applier.

`core.md` is the constitutional layer — loaded in full, every session. The setup
pass (docs/CORE_SETUP.md) derives a personalised one instead of shipping a blank
scaffold, and this module is the half that decides what reaches disk.

**Nothing generated is applied.** The pass produces a *proposal* under
`config.PROPOSALS_DIR`, exactly as the daily synthesis loop already does, and the
user approves it before a byte reaches `core.md`. `write_proposal` therefore
touches the proposals directory and nothing else; `apply_proposal` is the only
function here permitted to write `core.md`, and it cannot do so without an
explicit `approved` mapping handed to it by a caller acting on user approval. A
setup flow that wrote directly would be the one component in the system allowed
to silently overwrite the protocol layer, which is precisely backwards.

Approval is **per slot**, not all-or-nothing: a user who disagrees with one
inferred line should not have to reject the whole draft to fix it. So the
proposal carries one approval checkbox per slot, and `approved` carries only the
slots the user accepted — the rest render as they were (`[FILL]` for the stated
ones), which is a visible gap rather than an invented answer.

**Set aside, never destroy.** If a `core.md` already exists, it is moved to a
dated copy under `config.ORIGINALS_DIR` before the new one is written — the same
pattern `refresh_doc` in `install.sh` uses when it replaces the knowledge README.
That happens unconditionally and there is no parameter to disable it. Nothing
here overwrites in place and nothing here deletes.

Drafting itself happens in Claude Code, not in this module: gigabite has no model
and retrieval is the half it owns (CORE_SETUP §4). Where a draft line exists it is
passed in via `drafts` and rendered beside the evidence that produced it, so the
user confirms a claim about themselves rather than accepting an assertion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .. import config, util
from . import core_coverage, core_slots

# One proposal per day, named so it never collides with the synthesis proposal
# that shares the directory.
FILENAME_PREFIX = "core-setup"

DRAFT_PLACEHOLDER = (
    "_No drafted line yet — draft it in a `/gg` session from the evidence below._"
)


def _proposals_dir() -> Path:
    # Resolved per call, so a repointed knowledge base is honoured (see synthesis).
    return config.proposals_dir()


def _originals_dir() -> Path:
    # Likewise late-resolving. This is the installer's set-aside directory.
    return config.ORIGINALS_DIR


def _core_file() -> Path:
    return config.CORE_FILE


def _state(coverage) -> str:
    """Coverage state as a plain lowercase string, whatever type it is."""
    raw = getattr(coverage, "state", "")
    value = getattr(raw, "value", None) or getattr(raw, "name", None) or raw
    return str(value).strip().lower()


def _slot(slot_id: str):
    try:
        return core_slots.get_slot(slot_id)
    except (KeyError, LookupError, ValueError):
        return None


def _heading_block(text: str, title: str) -> str | None:
    """The body under the first markdown heading whose text contains *title*.

    Used only to show what a slot currently says in an existing `core.md`. A miss
    is reported as "would be added", never guessed at.
    """
    if not text or not title:
        return None
    needle = title.strip().lower()
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") and needle in line.lower():
            start = i + 1
            break
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start:]:
        if line.lstrip().startswith("#"):
            break
        body.append(line)
    return "\n".join(body).strip() or None


def _quote(text: str, limit: int = 12) -> list[str]:
    """Markdown blockquote lines, truncated so the proposal stays readable."""
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    clipped = lines[:limit]
    out = [f"> {ln}" if ln else ">" for ln in clipped]
    if len(lines) > limit:
        out.append("> …")
    return out


def _evidence_lines(coverage) -> list[str]:
    lines: list[str] = []
    for ev in getattr(coverage, "evidence", ()) or ():
        label = config.SOURCE_LABELS.get(ev.source, ev.source)
        date = util.short_date(ev.created_utc) if ev.created_utc else ""
        stamp = f"{label}, {date}" if date else label
        title = ev.title or "(untitled)"
        lines.append(f"- **{title}** — {stamp}  ")
        lines.append(f"  `{ev.doc_id}`")
        snippet = " ".join((ev.snippet or "").split())
        if snippet:
            lines.append(f"  > {snippet}")
        if ev.why:
            lines.append(f"  _{ev.why}_")
        lines.append("")
    if not lines:
        lines.append("_No evidence in the index for this slot._")
        lines.append("")
    return lines


def build_proposal(
    coverages,
    *,
    existing_core_md: str | None = None,
    drafts: Mapping[str, str] | None = None,
) -> str:
    """Render the review document for a set of slot coverages.

    One section per slot, each with its own approval checkbox. Evidenced slots
    show the drafted line with the evidence quoted beside it; thin and empty
    slots show the interview question instead of a draft, because there is
    nothing yet to confirm. Shipped slots show their final text as something the
    user may edit rather than something they must decide.

    `drafts` maps slot_id -> a drafted markdown body, supplied by the Claude Code
    half that does the writing; this module never invents one.

    If `existing_core_md` is given, each slot also shows what it says today, so
    the user reviews a change rather than only new text.
    """
    drafts = dict(drafts or {})
    coverages = tuple(coverages)  # may arrive as a generator; we walk it twice
    generated = datetime.now(timezone.utc).isoformat()
    date = util.short_date(generated)

    lines: list[str] = []
    lines.append(f"# Core setup proposal — {date}")
    lines.append("")
    lines.append(f"Generated {generated}. Slots: {len(coverages)}.")
    lines.append("")
    lines.append(
        "> GATED. Nothing in this file is applied automatically. Tick a slot to "
        "approve it, edit its text if it is not quite right, then apply the "
        "approved slots from a `/gg` session. Approval is per slot — rejecting "
        "one line does not reject the draft. Until you apply them, `core.md` is "
        "untouched."
    )
    lines.append("")
    if existing_core_md is not None:
        lines.append(
            "An existing `core.md` was found. Applying anything sets the current "
            "file aside as a dated copy first — it is never overwritten in place."
        )
        lines.append("")

    for coverage in coverages:
        slot_id = coverage.slot_id
        slot = _slot(slot_id)
        state = _state(coverage)

        title = getattr(slot, "title", "") or slot_id
        section = getattr(slot, "section", "")
        kind = getattr(slot, "kind", "")
        kind_name = str(getattr(kind, "value", None) or getattr(kind, "name", None)
                        or kind).strip().lower()

        header = f"## {title} — `{slot_id}`"
        lines.append(header)
        lines.append("")
        meta = [f"section {section}" if section else "", kind_name, state]
        lines.append("_" + " · ".join(p for p in meta if p) + "_")
        lines.append("")
        lines.append(f"- [ ] Approve `{slot_id}`")
        lines.append("")

        if existing_core_md is not None:
            current = _heading_block(existing_core_md, title)
            lines.append("**Currently**")
            lines.append("")
            if current:
                lines.extend(_quote(current))
            else:
                lines.append("_Not present — this would be added._")
            lines.append("")

        draft = drafts.get(slot_id)
        if state == "shipped":
            lines.append("**Ships as** (an invariant — edit if you must, but it is")
            lines.append("how the tool is safe to operate, not a preference)")
            lines.append("")
            body = draft or getattr(slot, "shipped_text", "") or ""
            lines.extend(_quote(body) if body else ["_No shipped text._"])
            lines.append("")
        elif state == "evidenced":
            lines.append("**Proposed**")
            lines.append("")
            lines.extend(_quote(draft) if draft else [DRAFT_PLACEHOLDER])
            lines.append("")
            lines.append("**Evidence** — confirm this is a fair claim about you")
            lines.append("")
            lines.extend(_evidence_lines(coverage))
        else:
            question = getattr(slot, "question", "") or ""
            lines.append(f"**Question** ({state} — not enough to draft from)")
            lines.append("")
            lines.append(question or "_No question recorded for this slot._")
            lines.append("")
            if getattr(coverage, "evidence", ()):
                lines.append("**Partial signal**")
                lines.append("")
                lines.extend(_evidence_lines(coverage))

    return "\n".join(lines).rstrip() + "\n"


def write_proposal(store, *, path=None, limit_per_slot: int = 5) -> Path:
    """Run the coverage pass and write the proposal. Returns its path.

    Idempotent for a given date: the same-day file is overwritten. Touches
    `config.PROPOSALS_DIR` only — this function never reads its way into writing
    `core.md`, and the test suite pins that.
    """
    coverages = core_coverage.assess(store, limit_per_slot=limit_per_slot)

    core_file = _core_file()
    existing = None
    if core_file.exists():
        existing = core_file.read_text(encoding="utf-8")

    body = build_proposal(coverages, existing_core_md=existing)

    if path is not None:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = _proposals_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        date = util.short_date(datetime.now(timezone.utc).isoformat())
        out = out_dir / f"{FILENAME_PREFIX}-{date}.md"

    out.write_text(body, encoding="utf-8")
    return out


def set_aside(core_path: Path) -> Path | None:
    """Move an existing `core.md` to a dated copy and return where it went.

    Mirrors `refresh_doc` in `install.sh`: `replaced-YYYY-MM-DD-core.md` under the
    machinery's originals directory, so the replaced copy is kept without
    appearing in the knowledge base as a stray file. Never overwrites an earlier
    set-aside copy — a second apply on the same day gets its own suffix.
    """
    if not core_path.exists():
        return None
    kept_dir = _originals_dir()
    kept_dir.mkdir(parents=True, exist_ok=True)
    date = util.short_date(datetime.now(timezone.utc).isoformat())
    stem = f"replaced-{date}-{core_path.name}"
    kept = kept_dir / stem
    n = 2
    while kept.exists():
        kept = kept_dir / f"replaced-{date}-{n}-{core_path.name}"
        n += 1
    core_path.replace(kept)
    return kept


def apply_proposal(approved: Mapping[str, str], *, core_path=None) -> Path:
    """Write `core.md` from the slots the user approved. The only writer here.

    `approved` maps slot_id -> the final markdown body for that slot and must be
    supplied explicitly by a caller acting on user approval. There is no default,
    no inference and no "apply everything" path: a slot absent from the mapping
    is rendered as it was, which for a stated slot means `[FILL]`.

    An empty mapping is a no-op — it returns the path without touching the file,
    so "the user approved nothing" can never truncate an existing protocol.

    An existing file is *always* set aside as a dated copy before the new one is
    written. There is no flag to skip it: the user's protocol is not something a
    caller may decide is not worth keeping, and the one time it matters is the
    one time someone would have turned it off. Nothing is overwritten in place
    and nothing is deleted.
    """
    target = Path(core_path) if core_path is not None else _core_file()

    if not approved:
        return target

    body = core_slots.render_core_md(dict(approved))

    set_aside(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def list_proposals() -> list[Path]:
    """Existing core-setup proposals, oldest first. Empty if none written yet."""
    out_dir = _proposals_dir()
    if not out_dir.exists():
        return []
    return sorted(out_dir.glob(f"{FILENAME_PREFIX}-*.md"))
