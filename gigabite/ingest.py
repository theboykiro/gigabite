"""Orchestrate ingestion across all sources."""

from __future__ import annotations

from typing import Iterable, Optional

from . import config
from .sources import IngestReport, claude_ai, claude_code, meetings, notes
from .store import Store

# Order matters, and 'note' must stay last.
#
# The note ingester scans the knowledge base, where a file may be a readable
# rendering of a document one of the other sources owns (features.materialize).
# It settles that by asking whether the document is already indexed, so the source
# it came from has to have had its turn first. Run out of order, the rendering
# would claim the document and the raw export would then overwrite it on the same
# pass — the same content written twice for no gain.
_INGESTERS = {
    config.SOURCE_CLAUDE_CODE: claude_code.ingest,
    config.SOURCE_CLAUDE_AI: claude_ai.ingest,
    config.SOURCE_MEETING: meetings.ingest,
    config.SOURCE_NOTE: notes.ingest,
}


def run(store: Store, sources: Optional[Iterable[str]] = None,
        force: bool = False) -> dict[str, IngestReport]:
    """Ingest local sources. Nothing here reaches the network.

    There is no filing step. ``~/Knowledge`` is where content is put and where it
    is read from, so a file is indexed where it sits.
    """
    selected = [src for src in (list(sources) if sources else list(_INGESTERS.keys()))
                if src in _INGESTERS]
    reports: dict[str, IngestReport] = {}

    if selected:
        # An older version archived documents untouched for 30 days, which hid
        # them from recall whenever anything active matched. Bring them back.
        store.restore_archived()

    for src in selected:
        reports[src] = _INGESTERS[src](store, force=force)
    return reports
