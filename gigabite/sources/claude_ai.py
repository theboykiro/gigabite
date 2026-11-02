"""Import exported Claude.ai (Anthropic account data export) conversations.

Anthropic's data export is a .zip (or an unpacked folder) containing
`conversations.json`: a list of conversations, each with `chat_messages`; the
Safari script (install/scripts/claude-ai-safari-export.js) downloads that same
file on its own. Browser chats can't be pulled programmatically, so an export is
the supported path, and making one is the only manual step: every ingest picks
the newest export out of ~/Downloads, moves it into the imports folder, and
nudges once a day when the last one is more than a week old (see `pickup`).

Schema handled (defensively — Anthropic has tweaked field names over time):
    conversation: uuid | name/title | created_at | updated_at | chat_messages
    message:      sender/role ("human"|"assistant") | text | content[] | created_at
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from .. import config, util
from ..store import Document, Message, Store
from . import IngestReport, project_alias


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

    Thin wrapper kept for readability at the call site; the shared implementation
    lives in ``sources.project_alias`` because Claude Code needs it too.
    """
    return project_alias(project)


def _msg_role(m: dict) -> str:
    sender = m.get("sender") or m.get("role") or ""
    return "user" if sender in ("human", "user") else ("assistant" if sender else "user")


def _msg_origin(m: dict, role: str) -> str:
    """Provenance for one claude.ai message (see util.ORIGIN_*).

    Weaker than the Claude Code case, and that is a property of the export rather
    than a shortcut: a browser conversation has no tool results replayed under
    the human's role, so a human message is typed unless its own blocks say
    otherwise (which `util.message_origin` decides), and an attachment is the
    file's prose rather than the user's.
    """
    if role != "user":
        return util.ORIGIN_NONE
    if isinstance(m.get("text"), str) and m["text"].strip():
        return util.ORIGIN_TYPED
    content = m.get("content")
    if content:
        return util.message_origin(content)
    if m.get("attachments"):
        return util.ORIGIN_REPLAYED
    return util.ORIGIN_TYPED


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
        role = _msg_role(m)
        messages.append(
            Message(
                seq=i,
                role=role,
                text=text,
                ts_utc=util.to_iso_utc(m.get("created_at")),
                origin=_msg_origin(m, role),
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


def _export_files(imports: Path) -> Iterable[Path]:
    for pat in ("*.zip", "*.json"):
        yield from sorted(imports.glob(pat))


# ---- pickup: from ~/Downloads into the imports folder ------------------------
#
# Each export is the whole history, so the newest one replaces what the folder
# holds rather than sitting beside it: two exports side by side are read in name
# order, and an older file sorting last would overwrite newer chats. The one it
# replaces is kept as `<name>.replaced-<date>` until the next pickup, so a bad
# export can be undone by renaming it back.

def _is_claude_export(conversations) -> bool:
    """A claude.ai export, not another tool's conversations.json (ChatGPT's has
    `mapping`, not `chat_messages`)."""
    return (isinstance(conversations, list) and bool(conversations)
            and isinstance(conversations[0], dict) and "chat_messages" in conversations[0])


def _candidates(downloads: Path) -> Iterable[Path]:
    """What in Downloads could be an export: the Safari script's
    conversations.json (renamed `conversations 2.json` and the like when one is
    already there), Anthropic's .zip, and that .zip unpacked by Safari."""
    yield from downloads.glob("conversations*.json")
    yield from downloads.glob("*.zip")
    yield from downloads.glob("*/conversations.json")


def pickup(box: Path, downloads: Path) -> Optional[Path]:
    """Move the newest claude.ai export in `downloads` into `box`, if it is newer
    than the one `box` already holds. Returns where it landed, or None."""
    if not downloads.is_dir():
        return None
    # glob swallows a permission error and finds nothing; listing first makes
    # macOS denying access to Downloads an error the ingest report shows.
    next(downloads.iterdir(), None)
    current = max((p.stat().st_mtime for p in _export_files(box)), default=0.0)
    found = []
    for path in _candidates(downloads):
        try:
            mtime = path.stat().st_mtime
            if mtime > current and _is_claude_export(_load_conversations(path)):
                found.append((mtime, path))
        except Exception:
            continue                    # unreadable, or not an export: leave it be
    if not found:
        return None
    _, newest = max(found)

    stamp = date.today().isoformat()
    for old in (*box.glob("*.json.replaced-*"), *box.glob("*.zip.replaced-*")):
        old.unlink()
    for old in list(_export_files(box)):
        old.rename(old.with_name(f"{old.name}.replaced-{stamp}"))
    # A loose json is renamed to the name the docs and the Safari script use.
    dest = box / (newest.name if newest.suffix.lower() == ".zip" else "conversations.json")
    shutil.move(str(newest), str(dest))
    return dest


def export_age_days(box: Path) -> Optional[int]:
    """Whole days since the newest export in `box` was made; None if there is none."""
    newest = max((p.stat().st_mtime for p in _export_files(box)), default=None)
    return None if newest is None else int((time.time() - newest) // 86400)


def _nudge(age: int) -> None:
    """A macOS notification that the export is stale, at most once a day."""
    if not config.NOTIFY or sys.platform != "darwin":
        return
    stamp = config.CLAUDE_AI_NUDGE_STAMP
    today = date.today().isoformat()
    try:
        if stamp.read_text().strip() == today:
            return
    except OSError:
        pass
    text = (f"claude.ai chats were last exported {age} days ago. Export again; "
            "gigabite picks the file up from Downloads.")
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        f'display notification "{text}" with title "gigabite"'],
                       capture_output=True, timeout=10)
        stamp.write_text(today + "\n")
    except Exception:
        pass


def ingest(store: Store, imports: Optional[Path] = None, force: bool = False) -> IngestReport:
    report = IngestReport(source=config.SOURCE_CLAUDE_AI)
    box = Path(imports) if imports else config.SOURCES_CLAUDE_AI
    # Only the real imports folder takes from Downloads: an explicit `imports`
    # is a test or a one-off, and must never move the user's files.
    live = imports is None
    if live and box.exists():
        try:
            landed = pickup(box, config.DOWNLOADS_DIR)
        except OSError as e:
            report.errors.append(f"could not pick up an export from {config.DOWNLOADS_DIR}: {e}")
        else:
            if landed:
                report.notes.append(f"picked up {landed.name} from {config.DOWNLOADS_DIR}")
    if not box.exists():
        report.notes.append(f"no claude.ai export at {box}")
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
    age = export_age_days(box)
    if live and age is not None and age > config.CLAUDE_AI_STALE_DAYS:
        report.notes.append(
            f"last export is {age} days old: claude.ai → Settings → Privacy → Export data "
            "(or the Safari script), then the next ingest picks it up from Downloads")
        _nudge(age)
    return report
