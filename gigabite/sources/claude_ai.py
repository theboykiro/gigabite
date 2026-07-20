"""Import exported Claude.ai (Anthropic account data export) conversations.

Anthropic's data export is a .zip (or an unpacked folder) containing
`conversations.json`: a list of conversations, each with `chat_messages`.
Drop the .zip or conversations.json into ~/.knowledge/_inbox/claude_ai/ and
run ingest. Browser chats can't be pulled programmatically, so this is the
supported path.

Schema handled (defensively — Anthropic has tweaked field names over time):
    conversation: uuid | name/title | created_at | updated_at | chat_messages
    message:      sender/role ("human"|"assistant") | text | content[] | created_at
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Iterable, Optional

from .. import config, util
from ..store import Document, Message, Store
from . import IngestReport


def _signature(path: Path) -> str:
    st = path.stat()
    return f"{int(st.st_mtime)}:{st.st_size}"


def _load_conversations(path: Path) -> list:
    """Return the list of conversation dicts from a .json or .zip export."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            name = None
            for candidate in zf.namelist():
                if candidate.endswith("conversations.json"):
                    name = candidate
                    break
            if name is None:
                return []
            with zf.open(name) as fh:
                data = json.load(io.TextIOWrapper(fh, encoding="utf-8"))
    else:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)

    if isinstance(data, dict):
        # some exports wrap the list, e.g. {"conversations": [...]}
        for key in ("conversations", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data if isinstance(data, list) else []


def _alias(project: str) -> str:
    """Map a claude.ai project label to your canonical short name.

    Aliases live in ~/.knowledge/_aliases.json, e.g.
        {"Acme Product Manager": "acme"}
    so grouping survives future browser refreshes. Missing file -> no-op.
    """
    if not project:
        return project
    path = config.KNOWLEDGE_DIR / "_aliases.json"
    try:
        import json as _json
        aliases = _json.loads(path.read_text(encoding="utf-8"))
        return aliases.get(project, project)
    except Exception:
        return project


def _msg_role(m: dict) -> str:
    sender = m.get("sender") or m.get("role") or ""
    return "user" if sender in ("human", "user") else ("assistant" if sender else "user")


def _msg_text(m: dict) -> str:
    if isinstance(m.get("text"), str) and m["text"].strip():
        return util.clean_text(m["text"])
    # newer exports use content blocks
    t = util.coalesce_blocks(m.get("content"))
    if t.strip():
        return util.clean_text(t)
    # attachments with extracted_content
    parts = []
    for att in (m.get("attachments") or []):
        if isinstance(att, dict) and att.get("extracted_content"):
            parts.append(str(att["extracted_content"]))
    return util.clean_text("\n".join(parts))


def conversation_to_document(conv: dict, ref: str) -> Optional[Document]:
    uuid = conv.get("uuid") or conv.get("id") or conv.get("conversation_id")
    if not uuid:
        return None
    title = conv.get("name") or conv.get("title") or ""
    created = util.to_iso_utc(conv.get("created_at"))
    updated = util.to_iso_utc(conv.get("updated_at")) or created

    raw_msgs = conv.get("chat_messages") or conv.get("messages") or []
    messages: list[Message] = []
    for i, m in enumerate(raw_msgs):
        if not isinstance(m, dict):
            continue
        text = _msg_text(m)
        if not text:
            continue
        messages.append(
            Message(
                seq=i,
                role=_msg_role(m),
                text=text,
                ts_utc=util.to_iso_utc(m.get("created_at")),
            )
        )
    if not messages:
        return None

    if not title:
        first_user = next((m.text for m in messages if m.role == "user"), messages[0].text)
        title = first_user.splitlines()[0][:80]

    # project tagging: the browser export stamps project_name; fall back to any
    # project_uuid present (better than nothing) so project chats are grouped.
    project = conv.get("project_name") or conv.get("project") or ""
    if not project and conv.get("project_uuid"):
        project = str(conv["project_uuid"])[:8]
    project = _alias(project)  # normalise long claude.ai labels to your short names
    return Document(
        source=config.SOURCE_CLAUDE_AI,
        native_id=str(uuid),
        title=title.strip(),
        project=project,
        created_utc=created,
        updated_utc=updated,
        ref=ref,
        extra={"export": ref, "uuid": str(uuid),
               **({"project_uuid": conv["project_uuid"]} if conv.get("project_uuid") else {})},
        messages=messages,
    )


def _export_files(inbox: Path) -> Iterable[Path]:
    for pat in ("*.zip", "*.json"):
        yield from sorted(inbox.glob(pat))


def ingest(store: Store, inbox: Optional[Path] = None, force: bool = False) -> IngestReport:
    report = IngestReport(source=config.SOURCE_CLAUDE_AI)
    box = Path(inbox) if inbox else config.INBOX_CLAUDE_AI
    if not box.exists():
        report.notes.append(f"no Claude.ai inbox at {box}")
        return report

    files = list(_export_files(box))
    if not files:
        report.notes.append(
            f"drop your Anthropic data export (conversations.json or the .zip) into {box}"
        )
        return report

    for path in files:
        sig = _signature(path)
        key = str(path)
        if not force and store.get_signature(config.SOURCE_CLAUDE_AI, key) == sig:
            report.notes.append(f"{path.name}: unchanged since last import")
            continue
        try:
            convs = _load_conversations(path)
        except Exception as e:
            report.errors.append(f"{path.name}: could not read export ({e})")
            continue
        for conv in convs:
            if not isinstance(conv, dict):
                continue
            report.scanned += 1
            try:
                doc = conversation_to_document(conv, ref=str(path))
                if doc is None:
                    report.skipped += 1
                    continue
                if store.upsert_document(doc):
                    report.changed += 1
                else:
                    report.skipped += 1
            except Exception as e:
                report.errors.append(f"{path.name}:{conv.get('uuid','?')}: {e}")
        store.set_signature(config.SOURCE_CLAUDE_AI, key, sig)

    store.commit()
    return report
