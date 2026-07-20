"""Calendar awareness from a pasted screenshot (ARCHITECTURE §7.2).

The calendar lives in a managed environment with no AI access, so the input is a
screenshot. Vision is the model's job, not the code's: in a Claude Code turn the
model reads the pasted image and extracts the meetings as JSON; this module takes
that structured list, maps each meeting to a project/context, files it as a
searchable document, and attaches prep (recall) for the meeting ahead.

A meeting dict looks like:
    {"title": "...", "start": "2026-07-21T14:00", "end": "...",
     "attendees": ["a", "b"], "location": "...", "notes": "..."}
Only `title` is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .. import config, util
from ..store import Document, Message, Store
from . import routing, save

SOURCE = config.SOURCE_CALENDAR


def _meeting_text(m: dict) -> str:
    parts = [str(m.get("title", "")).strip()]
    if m.get("start"):
        span = str(m["start"])
        if m.get("end"):
            span += f" – {m['end']}"
        parts.append(f"When: {span}")
    att = m.get("attendees")
    if att:
        parts.append("Attendees: " + (", ".join(att) if isinstance(att, list) else str(att)))
    if m.get("location"):
        parts.append(f"Location: {m['location']}")
    if m.get("notes"):
        parts.append(str(m["notes"]))
    return "\n".join(p for p in parts if p)


def add_meetings(store: Store, meetings: list, *, projects: Optional[list] = None) -> list:
    """Ingest a list of meeting dicts. Returns the doc_ids added/updated."""
    projects = projects if projects is not None else routing._scan_projects()
    ids = []
    for m in meetings:
        if not isinstance(m, dict) or not str(m.get("title", "")).strip():
            continue
        title = str(m["title"]).strip()
        start_iso = util.to_iso_utc(m.get("start")) or ""
        # detect project from the title + attendees
        blob = title + " " + " ".join(m["attendees"]) if isinstance(m.get("attendees"), list) else title
        ctx = routing.resolve_context(blob, projects=projects)
        day = (start_iso or "")[:10] or "undated"
        native = f"{day}-{save.slugify(title) or 'meeting'}"
        doc = Document(
            source=SOURCE,
            native_id=native,
            title=title,
            project=ctx["project"] or "",
            created_utc=start_iso,
            updated_utc=start_iso,
            ref="calendar screenshot",
            extra={k: m[k] for k in ("attendees", "location", "end") if m.get(k)},
            messages=[Message(seq=0, role="meeting", text=_meeting_text(m), ts_utc=start_iso)],
        )
        if store.upsert_document(doc):
            ids.append(doc.doc_id)
    store.commit()
    return ids


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def agenda(store: Store, *, day: Optional[str] = None, prep_per: int = 3) -> list:
    """Return upcoming meetings with prep attached.

    day: None/'next' -> from now forward; 'today' -> today's date;
         'YYYY-MM-DD' -> that date; 'all' -> everything.
    Each item: {meeting: <document>, prep: [<search hits>]}.
    """
    rows = [d for d in store.iter_documents(include_historical=True) if d["source"] == SOURCE]
    today = _now_iso()[:10]
    if day in (None, "next"):
        rows = [r for r in rows if (r["created_utc"] or "") >= today or not r["created_utc"]]
    elif day == "today":
        rows = [r for r in rows if (r["created_utc"] or "")[:10] == today]
    elif day and day != "all":
        rows = [r for r in rows if (r["created_utc"] or "")[:10] == day]
    rows.sort(key=lambda r: r["created_utc"] or "9999")

    # prep is drawn from real history, never from calendar entries — exclude the
    # calendar source at query level (otherwise a meeting's own title AND-matches
    # itself and masks the OR fallback that would surface prior context).
    prep_sources = [s for s in config.ALL_SOURCES if s != SOURCE]
    out = []
    for r in rows:
        hits = store.search(r["title"], project=r["project"] or None,
                            sources=prep_sources, limit=prep_per, record=False)
        out.append({"meeting": r, "prep": hits})
    return out
