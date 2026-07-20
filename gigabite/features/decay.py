"""Reference-frequency decay (ARCHITECTURE §6).

Active context should be a function of what you actually touch, not of
everything ever recorded. This job iterates the index and archives documents
whose last-touch is older than a decay window — archival being nothing more
than flipping the index's `active` flag to 0.

Non-destructive by design:
  - Nothing is deleted; archived rows stay in the index and remain searchable
    on explicit request (`search(..., include_historical=True)`, surfaced on the
    CLI as `gigabite search --all`).
  - Restore-on-access: when a search actually returns an archived document,
    `Store.record_access` flips it back to `active = 1` and refreshes
    `accessed_utc`. This fires whenever archived rows are in the result set —
    i.e. under `--all`, and under the CLI's automatic fallback where a default
    search with no active matches retries across archived docs. A default search
    that already has active matches does NOT surface (or restore) archived docs,
    which is the point of decay. Recovery by id is also available via
    `decay.restore(store, doc_id)` / `gigabite decay --restore <doc_id>`.

Last-touch is `accessed_utc`, falling back to `updated_utc`, then `created_utc`
— the most recent signal that a document still matters. Documents with no usable
timestamp are left active (conservative: we never archive what we can't date).

The default window is 30 days. ARCHITECTURE names 14 as a starting point; we
open wider on purpose and let it tune down against real usage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import util

DEFAULT_WINDOW_DAYS = 30


def _last_touch(doc: dict) -> str:
    """Most recent activity signal for a document, normalised to ISO UTC.

    Returns '' when the document carries no parseable timestamp at all.
    """
    for key in ("accessed_utc", "updated_utc", "created_utc"):
        raw = doc.get(key)
        if raw:
            iso = util.to_iso_utc(raw)
            if iso:
                return iso
    return ""


def run(store, window_days: int = DEFAULT_WINDOW_DAYS, dry_run: bool = True) -> dict:
    """Archive active documents untouched for longer than `window_days`.

    In dry_run mode (the default) nothing is changed — the returned 'archived'
    list is exactly what a real run would archive.
    """
    cutoff = util.to_iso_utc(
        (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    )

    archived: list[dict] = []
    for doc in store.iter_documents(include_historical=False):
        # include_historical=False already restricts to active=1, but guard anyway.
        if not doc.get("active", 1):
            continue
        touch = _last_touch(doc)
        if not touch:
            continue  # undateable -> leave active (conservative)
        if touch < cutoff:
            archived.append(
                {
                    "doc_id": doc["doc_id"],
                    "title": doc.get("title") or "",
                    "source": doc.get("source") or "",
                    "project": doc.get("project") or "",
                    "last_touch": touch,
                }
            )
            if not dry_run:
                store.set_active(doc["doc_id"], False)

    return {
        "archived": archived,
        "count": len(archived),
        "window_days": window_days,
        "cutoff_utc": cutoff,
        "dry_run": dry_run,
    }


def restore(store, doc_id: str) -> bool:
    """Restore an archived document to active. True if the doc exists."""
    doc = store.get_document(doc_id)
    if doc is None:
        return False
    store.set_active(doc_id, True)
    return True


def status(store, oldest: int = 10) -> dict:
    """Snapshot of the active/archived split and the stalest active documents."""
    docs = store.iter_documents(include_historical=True)
    active = [d for d in docs if d.get("active", 1)]
    archived = [d for d in docs if not d.get("active", 1)]

    dated = [(d, _last_touch(d)) for d in active]
    dated = [(d, t) for (d, t) in dated if t]
    dated.sort(key=lambda pair: pair[1])  # ascending -> oldest first

    return {
        "active": len(active),
        "archived": len(archived),
        "total": len(docs),
        "oldest_active": [
            {
                "doc_id": d["doc_id"],
                "title": d.get("title") or "",
                "source": d.get("source") or "",
                "project": d.get("project") or "",
                "last_touch": t,
            }
            for (d, t) in dated[:oldest]
        ],
    }
