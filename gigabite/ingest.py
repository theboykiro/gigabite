"""Orchestrate ingestion across all sources."""

from __future__ import annotations

from typing import Iterable, Optional

from . import config
from .sources import IngestReport, claude_ai, claude_ai_live, claude_code, meetings, notes
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


def run(store: Store, sources: Optional[Iterable[str]] = None, force: bool = False,
        remote: bool = False) -> dict[str, IngestReport]:
    """Ingest local sources, then (only with remote=True) the live claude.ai pull.

    Local-only by default: the live pull is opt-in (`gigabite claude-sync` or
    `ingest --remote`) because claude.ai's API is Cloudflare-gated for non-browser
    clients, so running it on every refresh would just add latency and noise.

    There is no filing step. ``~/Knowledge`` is where content is put and where it
    is read from, so a file is indexed where it sits.
    """
    selected = list(sources) if sources else list(_INGESTERS.keys())
    reports: dict[str, IngestReport] = {}

    for src in selected:
        fn = _INGESTERS.get(src)
        if fn is None:
            continue
        reports[src] = fn(store, force=force)

    if remote and (sources is None or config.SOURCE_CLAUDE_AI in selected):
        live = claude_ai_live.ingest(store, force=force)
        if config.SOURCE_CLAUDE_AI in reports:
            reports[config.SOURCE_CLAUDE_AI].merge(live)  # combine export + live tallies
        else:
            reports[config.SOURCE_CLAUDE_AI] = live
    return reports
