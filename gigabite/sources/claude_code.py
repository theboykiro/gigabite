"""Ingest Claude Code sessions from ~/.claude/projects/**/*.jsonl.

Each .jsonl file is one session. Lines are newline-delimited JSON events; we
keep user / assistant / attachment events and reconstruct the conversation in
file order. Project is derived from the session's `cwd`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .. import config, util
from ..store import Document, Message, Store
from . import IngestReport

# event types that carry conversational text
_CONTENT_TYPES = {"user", "assistant", "attachment"}


def _signature(path: Path) -> str:
    st = path.stat()
    return f"{int(st.st_mtime)}:{st.st_size}"


def _project_from_cwd(cwd: str) -> str:
    if not cwd:
        return ""
    return Path(cwd).name or cwd


def _attachment_text(ev: dict) -> str:
    """Pull any pasted/attached text out of an attachment event."""
    att = ev.get("attachment")
    if att is None:
        return ""
    if isinstance(att, str):
        return att
    if isinstance(att, dict):
        for key in ("text", "content", "value", "data"):
            v = att.get(key)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, list):
                t = util.coalesce_blocks(v)
                if t.strip():
                    return t
    return ""


def parse_session_file(path: Path) -> Optional[Document]:
    """Parse one .jsonl session into a Document, or None if it has no content."""
    # Identity is the FILE, not the sessionId in events: subagent transcripts
    # carry the parent's sessionId, which would collapse many files onto one doc.
    native_id = path.stem
    session_id = path.stem
    title_custom = None
    title_ai = None
    cwd = ""
    messages: list[Message] = []
    seq = 0
    first_ts = ""
    last_ts = ""

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue

            etype = ev.get("type")
            if ev.get("sessionId"):
                session_id = ev["sessionId"]
            if not cwd and ev.get("cwd"):
                cwd = ev["cwd"]

            if etype == "custom-title":
                title_custom = ev.get("customTitle") or title_custom
                continue
            if etype == "ai-title":
                title_ai = ev.get("aiTitle") or title_ai
                continue
            if etype not in _CONTENT_TYPES:
                continue

            ts = util.to_iso_utc(ev.get("timestamp"))

            if etype == "attachment":
                text = util.clean_text(_attachment_text(ev))
                role = "attachment"
            else:
                msg = ev.get("message") or {}
                role = msg.get("role") or etype
                text = util.clean_text(util.coalesce_blocks(msg.get("content")))

            if not text:
                continue

            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            messages.append(Message(seq=seq, role=role, text=text, ts_utc=ts))
            seq += 1

    if not messages:
        return None

    title = title_custom or title_ai
    if not title:
        first_user = next((m.text for m in messages if m.role == "user"), messages[0].text)
        title = first_user.splitlines()[0][:80] if first_user else "Untitled session"

    return Document(
        source=config.SOURCE_CLAUDE_CODE,
        native_id=native_id,
        title=title.strip(),
        project=_project_from_cwd(cwd),
        created_utc=first_ts,
        updated_utc=last_ts or first_ts,
        ref=str(path),
        extra={"cwd": cwd, "session_id": session_id},
        messages=messages,
    )


def ingest(store: Store, projects_dir: Optional[Path] = None, force: bool = False) -> IngestReport:
    report = IngestReport(source=config.SOURCE_CLAUDE_CODE)
    root = Path(projects_dir) if projects_dir else config.CLAUDE_CODE_PROJECTS_DIR
    if not root.exists():
        report.notes.append(f"no Claude Code projects dir at {root}")
        return report

    for path in sorted(root.rglob("*.jsonl")):
        # Skip internal subagent transcripts — they're not the user's conversations.
        if path.name.startswith("agent-"):
            continue
        report.scanned += 1
        try:
            sig = _signature(path)
            key = str(path)
            if not force and store.get_signature(config.SOURCE_CLAUDE_CODE, key) == sig:
                report.skipped += 1
                continue
            doc = parse_session_file(path)
            if doc is None:
                store.set_signature(config.SOURCE_CLAUDE_CODE, key, sig)
                report.skipped += 1
                continue
            if store.upsert_document(doc):
                report.changed += 1
            else:
                report.skipped += 1
            store.set_signature(config.SOURCE_CLAUDE_CODE, key, sig)
        except Exception as e:  # keep going; report per-file failures
            report.errors.append(f"{path.name}: {e}")

    store.commit()
    return report
