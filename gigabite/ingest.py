"""Orchestrate ingestion across all sources."""

from __future__ import annotations

from typing import Iterable, Optional

from . import config
from .sources import IngestReport, claude_ai, claude_code, claude_memory, meetings, notes
from .store import Store


def _notes(store: Store, force: bool = False) -> IngestReport:
    """The knowledge base, plus Claude Code's auto-memory notes.

    Memory files are notes that happen to live outside ``~/Knowledge`` (see
    ``sources.claude_memory``), so they ride the same pass: every refresh, and
    every ``--source note`` after a save, picks them up. They go first because
    the knowledge-base scan is the one that has to see everything else indexed.
    """
    report = claude_memory.ingest(store, force=force)
    report.merge(notes.ingest(store, force=force))
    return report


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
    config.SOURCE_NOTE: _notes,
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
