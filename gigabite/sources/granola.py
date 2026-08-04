"""Ingest Granola meeting notes.

Granola v6 encrypts its entire local store (granola.db + *.enc) behind a
keychain-gated key, so notes cannot be read from disk without your one-time
authorization (see granola_live.py and GRANOLA.md). This module is the
guaranteed, secret-free path: drop exports into ~/Knowledge/_sources/granola/.

Accepts:
  - .md / .txt  — one file per meeting (title from YAML frontmatter, first
                  '# heading', or filename). Notes + transcript both indexed.
  - .json       — a single Granola document, a list of them, or an API-shaped
                  payload ({"docs":[...]}). Notes and transcript split out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from .. import config, util
from ..store import Document, Message, Store
from . import IngestReport

# ---------------------------------------------------------------------------
# markdown / text
# ---------------------------------------------------------------------------

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


def _title_from_markdown(body: str, fallback: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line:
            break
    return fallback


def document_from_markdown(path: Path) -> Optional[Document]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(raw)
    body = util.clean_text(body)
    if not body:
        return None
    title = meta.get("title") or _title_from_markdown(body, path.stem)
    created = util.to_iso_utc(meta.get("date") or meta.get("created") or meta.get("created_at"))
    native_id = meta.get("id") or meta.get("document_id") or path.name

    return Document(
        source=config.SOURCE_GRANOLA,
        native_id=str(native_id),
        title=title.strip(),
        project=meta.get("project", ""),
        created_utc=created,
        updated_utc=created,
        ref=str(path),
        extra={"file": str(path), **{k: v for k, v in meta.items() if k != "title"}},
        messages=[Message(seq=0, role="note", text=body, ts_utc=created)],
    )


# ---------------------------------------------------------------------------
# json (export or API shape) — also reused by the live connector
# ---------------------------------------------------------------------------

def _first(obj: dict, *keys):
    for k in keys:
        v = obj.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _notes_text(obj: dict) -> str:
    """Extract human-readable notes/summary from a Granola document object."""
    v = _first(obj, "notes_markdown", "notes_plain", "summary_markdown", "summary",
               "notes", "overview")
    if isinstance(v, str):
        return util.clean_text(v)
    if isinstance(v, (dict, list)):
        # ProseMirror / block json -> flatten any string leaves
        return util.clean_text(_flatten_strings(v))
    return ""


def _transcript_text(obj: dict) -> str:
    v = _first(obj, "transcript", "transcript_text", "transcription")
    if isinstance(v, str):
        return util.clean_text(v)
    if isinstance(v, list):
        # list of {speaker, text} segments
        segs = []
        for seg in v:
            if isinstance(seg, dict):
                spk = seg.get("speaker") or seg.get("source") or ""
                txt = seg.get("text") or seg.get("content") or ""
                segs.append(f"{spk}: {txt}".strip(": ").strip())
            elif isinstance(seg, str):
                segs.append(seg)
        return util.clean_text("\n".join(segs))
    return ""


def _flatten_strings(node) -> str:
    out: list[str] = []
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if isinstance(node.get("text"), str):
            out.append(node["text"])
        for v in node.values():
            if isinstance(v, (dict, list)):
                out.append(_flatten_strings(v))
    elif isinstance(node, list):
        for v in node:
            out.append(_flatten_strings(v))
    return "\n".join(p for p in out if p)


def document_from_granola_json(obj: dict, ref: str = "granola") -> Optional[Document]:
    if not isinstance(obj, dict):
        return None
    native_id = _first(obj, "id", "document_id", "uuid") or util.doc_id("granola_raw", json.dumps(obj, sort_keys=True)[:200])
    title = _first(obj, "title", "name") or "Untitled meeting"
    created = util.to_iso_utc(_first(obj, "created_at", "created", "date", "start_time"))
    updated = util.to_iso_utc(_first(obj, "updated_at", "updated")) or created

    messages: list[Message] = []
    notes = _notes_text(obj)
    if notes:
        messages.append(Message(seq=0, role="note", text=notes, ts_utc=created))
    transcript = _transcript_text(obj)
    if transcript:
        messages.append(Message(seq=1, role="transcript", text=transcript, ts_utc=created))
    if not messages:
        return None

    attendees = _first(obj, "attendees", "people", "participants")
    return Document(
        source=config.SOURCE_GRANOLA,
        native_id=str(native_id),
        title=str(title).strip(),
        project="",
        created_utc=created,
        updated_utc=updated,
        ref=ref,
        extra={"attendees": attendees} if attendees else {},
        messages=messages,
    )


def _iter_json_documents(data) -> Iterable[dict]:
    if isinstance(data, list):
        yield from (d for d in data if isinstance(d, dict))
    elif isinstance(data, dict):
        for key in ("docs", "documents", "data", "notes", "results"):
            if isinstance(data.get(key), list):
                yield from (d for d in data[key] if isinstance(d, dict))
                return
        yield data


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def _signature(path: Path) -> str:
    st = path.stat()
    return f"{int(st.st_mtime)}:{st.st_size}"


def ingest(store: Store, inbox: Optional[Path] = None, force: bool = False) -> IngestReport:
    report = IngestReport(source=config.SOURCE_GRANOLA)
    box = Path(inbox) if inbox else config.INBOX_GRANOLA
    if not box.exists():
        report.notes.append(f"no Granola inbox at {box}")
        return report

    def _reserved(path: Path) -> bool:
        # skip if any path segment (relative to the inbox) is hidden/reserved,
        # not just the filename — matches the notes source's behaviour
        rel = path.relative_to(box)
        return any(part.startswith((".", "_")) for part in rel.parts)

    files = [
        p for p in sorted(box.rglob("*"))
        if p.suffix.lower() in (".md", ".txt", ".json")
        and p.name.lower() != "readme.md"          # skip the inbox instructions
        and not _reserved(p)
    ]
    if not files:
        report.notes.append(
            f"no Granola exports yet — drop .md/.txt/.json into {box} "
            f"(or run `gigabite granola-connect` to pull live)"
        )
        return report

    for path in files:
        report.scanned += 1
        sig = _signature(path)
        key = str(path)
        if not force and store.get_signature(config.SOURCE_GRANOLA, key) == sig:
            report.skipped += 1
            continue
        try:
            if path.suffix.lower() == ".json":
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                changed_any = False
                for obj in _iter_json_documents(data):
                    doc = document_from_granola_json(obj, ref=str(path))
                    if doc and store.upsert_document(doc):
                        changed_any = True
                report.changed += 1 if changed_any else 0
                report.skipped += 0 if changed_any else 1
            else:
                doc = document_from_markdown(path)
                if doc and store.upsert_document(doc):
                    report.changed += 1
                else:
                    report.skipped += 1
            store.set_signature(config.SOURCE_GRANOLA, key, sig)
        except Exception as e:
            report.errors.append(f"{path.name}: {e}")

    store.commit()
    return report
