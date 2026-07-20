"""Orchestrate ingestion across all sources."""

from __future__ import annotations

from typing import Iterable, Optional

from . import config
from .sources import IngestReport, claude_ai, claude_ai_live, claude_code, granola, notes
from .store import Store

_INGESTERS = {
    config.SOURCE_CLAUDE_CODE: claude_code.ingest,
    config.SOURCE_CLAUDE_AI: claude_ai.ingest,
    config.SOURCE_GRANOLA: granola.ingest,
    config.SOURCE_NOTE: notes.ingest,
}


def run(store: Store, sources: Optional[Iterable[str]] = None, force: bool = False,
        remote: bool = True) -> dict[str, IngestReport]:
    """Ingest local sources, then (optionally) the live claude.ai pull.

    The live pull self-skips when no keychain token is set, so `remote=True`
    is safe as a default — it only does network work once you've opted in.
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
