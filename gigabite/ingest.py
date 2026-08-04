"""Orchestrate ingestion across all sources."""

from __future__ import annotations

from typing import Iterable, Optional

from . import config
from .features import inbox
from .sources import IngestReport, claude_ai, claude_ai_live, claude_code, granola, notes
from .store import Store

_INGESTERS = {
    config.SOURCE_CLAUDE_CODE: claude_code.ingest,
    config.SOURCE_CLAUDE_AI: claude_ai.ingest,
    config.SOURCE_GRANOLA: granola.ingest,
    config.SOURCE_NOTE: notes.ingest,
}


def _file_inbox(store: Store) -> dict:
    """Run the drop-folder filing pass. A failure here must never abort ingest.

    Uses a settle window: ingest runs unattended from launchd, so a file still
    being written must be left for the next pass rather than filed half-complete
    and moved out from under whatever is writing it.
    """
    try:
        return inbox.file_inbox(store, settle_seconds=inbox.SETTLE_SECONDS)
    except Exception as e:                       # unreadable dir, permissions, …
        return {"scanned": 0, "filed": [], "triaged": [], "waiting": [],
                "errors": [f"filing pass failed: {e}"]}


def _fold_filing_into(filing: dict, report: IngestReport) -> None:
    """Surface what the Inbox pass did through the note source's report."""
    for e in filing["filed"]:
        where = e["project"] + (f":{e['layer']}" if e["layer"] else "")
        report.notes.append(f"inbox: {e['origin']} → {where}")
    for e in filing["triaged"]:
        report.notes.append(f"inbox: {e['origin']} → _needs-triage ({e['reason']})")
    for rel in filing.get("waiting", ()):
        report.notes.append(f"inbox: {rel} → still being written, left for next pass")
    report.errors.extend(f"inbox: {err}" for err in filing["errors"])


def run(store: Store, sources: Optional[Iterable[str]] = None, force: bool = False,
        remote: bool = False) -> dict[str, IngestReport]:
    """Ingest local sources, then (only with remote=True) the live claude.ai pull.

    Local-only by default: the live pull is opt-in (`gigabite claude-sync` or
    `ingest --remote`) because claude.ai's API is Cloudflare-gated for non-browser
    clients, so running it on every refresh would just add latency and noise.
    """
    selected = list(sources) if sources else list(_INGESTERS.keys())
    reports: dict[str, IngestReport] = {}

    # File the Inbox/ drop folder BEFORE the note ingester, so anything dropped is
    # both filed into ~/Knowledge and indexed in this same pass.
    filing = _file_inbox(store) if config.SOURCE_NOTE in selected else None

    for src in selected:
        fn = _INGESTERS.get(src)
        if fn is None:
            continue
        reports[src] = fn(store, force=force)

    if filing is not None and config.SOURCE_NOTE in reports:
        _fold_filing_into(filing, reports[config.SOURCE_NOTE])

    if remote and (sources is None or config.SOURCE_CLAUDE_AI in selected):
        live = claude_ai_live.ingest(store, force=force)
        if config.SOURCE_CLAUDE_AI in reports:
            reports[config.SOURCE_CLAUDE_AI].merge(live)  # combine export + live tallies
        else:
            reports[config.SOURCE_CLAUDE_AI] = live
    return reports
