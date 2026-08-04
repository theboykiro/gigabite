"""Daily synthesis feedback loop (ARCHITECTURE §5), gated and deterministic.

This module does the mechanical half of synthesis: it collects the day's
recent documents and compresses them into a low-volume **digest**, then writes
a **proposal** file with empty approval checklists. It contains no LLM calls —
the semantic work (extracting decisions, constraints, shifted priorities) is
done later by Claude reading the digest, and the resulting changes are applied
only after a human accepts them.

Approval is manual and non-negotiable. This module NEVER writes to `core.md`
or to the knowledge base. Its only output is a proposal under
`~/Knowledge/_proposals/YYYY-MM-DD.md`. Nothing in that file is applied to the
operating system until the user (or a `/gg` session) reviews it and applies the
accepted items by hand. That gate is exactly what stops silent drift.

Volume discipline (§6): the digest carries a short compressed excerpt per
document (~400 chars), not full transcripts. Transcripts stay in their source
tool; the index and the digest hold distilled context only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config, util

EXCERPT_CHARS = 400
NO_PROJECT_LABEL = "(no project)"


def _proposals_dir() -> Path:
    return config.KNOWLEDGE_DIR / "_proposals"


def _excerpt(store, doc_id: str, limit: int = EXCERPT_CHARS) -> str:
    """A short, compressed excerpt: the head of the concatenated message text."""
    doc = store.get_document(doc_id)
    if not doc:
        return ""
    text = util.clean_text(
        " ".join(m.get("text", "") for m in doc.get("messages", []))
    )
    text = " ".join(text.split())  # single-line, collapse whitespace
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def build_digest(store, since_days: int = 1) -> dict:
    """Compress recent documents into a project-grouped digest.

    Recency is by `updated_utc`. Each entry keeps the title, source and a short
    excerpt — deliberately low-volume, not the full transcript.
    """
    since_iso = util.to_iso_utc(
        (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    )
    docs = store.recent_documents(since_iso, by="updated_utc")

    by_project: dict[str, list[dict]] = {}
    for doc in docs:
        project = doc.get("project") or NO_PROJECT_LABEL
        by_project.setdefault(project, []).append(
            {
                "doc_id": doc["doc_id"],
                "source": doc.get("source") or "",
                "title": doc.get("title") or "",
                "updated_utc": doc.get("updated_utc") or "",
                "excerpt": _excerpt(store, doc["doc_id"]),
            }
        )

    groups = [
        {"project": project, "documents": entries}
        for project, entries in sorted(by_project.items())
    ]

    return {
        "since_days": since_days,
        "since_iso": since_iso,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "document_count": len(docs),
        "groups": groups,
    }


def _render(digest: dict) -> str:
    date = util.short_date(digest["generated_utc"])
    lines: list[str] = []
    lines.append(f"# Daily synthesis proposal — {date}")
    lines.append("")
    lines.append(
        f"Generated {digest['generated_utc']}. Window: last "
        f"{digest['since_days']} day(s) (since {digest['since_iso']}). "
        f"Documents: {digest['document_count']}."
    )
    lines.append("")
    lines.append(
        "> GATED. Nothing in this file is applied automatically. Review each "
        "item, then apply accepted changes by hand (or in a `/gg` session). "
        "This proposal never writes to `core.md` or the knowledge base."
    )
    lines.append("")

    lines.append("## Digest")
    lines.append("")
    if not digest["groups"]:
        lines.append("_No documents updated in this window._")
        lines.append("")
    for group in digest["groups"]:
        lines.append(f"### {group['project']}")
        lines.append("")
        for entry in group["documents"]:
            label = config.SOURCE_LABELS.get(entry["source"], entry["source"])
            updated = util.short_date(entry["updated_utc"])
            title = entry["title"] or "(untitled)"
            lines.append(f"- **{title}** — {label}, updated {updated}  ")
            lines.append(f"  `{entry['doc_id']}`")
            if entry["excerpt"]:
                lines.append(f"  > {entry['excerpt']}")
            lines.append("")

    lines.append("## Proposed knowledge updates")
    lines.append("")
    lines.append(
        "<!-- Accepted knowledge-base updates per project/layer. Reference the "
        "doc_id above. Nothing is written until you apply it. -->"
    )
    lines.append("")
    lines.append("- [ ] ")
    lines.append("")

    lines.append("## Proposed core.md updates")
    lines.append("")
    lines.append(
        "<!-- Only where warranted — core.md changes are rare and high-bar. -->"
    )
    lines.append("")
    lines.append("- [ ] ")
    lines.append("")

    return "\n".join(lines)


def write_proposal(store, since_days: int = 1) -> Path:
    """Write today's synthesis proposal and return its path.

    Idempotent for a given date: the same-day file is overwritten. Only ever
    touches `~/Knowledge/_proposals/` — never core.md or the knowledge base.
    """
    digest = build_digest(store, since_days=since_days)
    out_dir = _proposals_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    date = util.short_date(digest["generated_utc"])
    path = out_dir / f"{date}.md"
    path.write_text(_render(digest), encoding="utf-8")
    return path


def list_proposals() -> list[Path]:
    """Existing proposal files, oldest first. Empty if none written yet."""
    out_dir = _proposals_dir()
    if not out_dir.exists():
        return []
    return sorted(out_dir.glob("*.md"))
