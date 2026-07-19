"""Orchestrate ingestion across all sources."""

from __future__ import annotations

from typing import Iterable, Optional

from . import config
from .sources import IngestReport, claude_ai, claude_code, granola
from .store import Store

_INGESTERS = {
    config.SOURCE_CLAUDE_CODE: claude_code.ingest,
    config.SOURCE_CLAUDE_AI: claude_ai.ingest,
    config.SOURCE_GRANOLA: granola.ingest,
}


def run(store: Store, sources: Optional[Iterable[str]] = None, force: bool = False) -> dict[str, IngestReport]:
    selected = list(sources) if sources else list(_INGESTERS.keys())
    reports: dict[str, IngestReport] = {}
    for src in selected:
        fn = _INGESTERS.get(src)
        if fn is None:
            continue
        reports[src] = fn(store, force=force)
    return reports
