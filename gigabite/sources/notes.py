"""Ingest saved notes from the central knowledge base.

Notes are markdown files written by ``features.save.save_note`` (or by hand)
under ``~/Knowledge/{project}/[{layer}/]``. This ingester scans that tree and
indexes each note so it turns up in search alongside Claude Code / Claude.ai /
Granola content.

What counts as a note:
  - any ``*.md`` under ``{project}/[{layer}/]``
  - EXCLUDING reserved top-level folders (leading '_' e.g. _sources / _archive /
    _proposals, or leading '.')
  - EXCLUDING the drop folder, which is named ``Inbox`` and so has no leading
    underscore to disqualify it (see ``_excluded_dirs``)
  - EXCLUDING README.md and _project.md (meta, not knowledge)
  - EXCLUDING any path segment starting with '_' or '.'

Each note becomes a Document with source='note', native_id = its path relative
to the knowledge root, project = the top folder, and the layer captured in extra.
Incremental via an ``mtime:size`` file signature under the 'note' source key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from .. import config, util
from ..store import Document, Message, Store
from . import IngestReport
# Reuse the frontmatter / title parsing already proven for markdown notes.
from .granola import _parse_frontmatter, _title_from_markdown

# config.py owns the canonical SOURCE_NOTE constant + label; use the literal here
# so this module stands alone if imported before that wiring lands.
SOURCE = "note"

_SKIP_FILES = {"readme.md", "_project.md"}


def _hidden(name: str) -> bool:
    return name.startswith(("_", "."))


def _excluded_dirs() -> set:
    """Top-level folders inside the knowledge base that are not projects.

    Reserved folders are recognised by their leading '_' or '.', but the drop
    folder is deliberately named ``Inbox`` so a human can find it, and that means
    it would otherwise look exactly like a project called "Inbox" — every file
    waiting to be filed would be indexed twice, once in the drop box and again
    after filing. It is excluded by resolved path rather than by name, since it
    is configurable and need not sit inside the knowledge base at all.
    """
    out = set()
    for d in (config.INBOX_DROP_DIR,):
        try:
            out.add(Path(d).resolve())
        except OSError:
            continue
    return out


def _iter_note_files(root: Path) -> Iterator[Path]:
    """Yield note files under each project dir, skipping reserved names."""
    excluded = _excluded_dirs()
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir() or _hidden(project_dir.name):
            continue
        try:
            if project_dir.resolve() in excluded:
                continue
        except OSError:
            pass
        for path in sorted(project_dir.rglob("*.md")):
            rel_parts = path.relative_to(project_dir).parts
            # any hidden/reserved segment (dir or file) disqualifies the file
            if any(_hidden(part) for part in rel_parts):
                continue
            if path.name.lower() in _SKIP_FILES:
                continue
            yield path


def _signature(path: Path) -> str:
    st = path.stat()
    return f"{int(st.st_mtime)}:{st.st_size}"


def document_from_note(path: Path, root: Path) -> Optional[Document]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(raw)
    body = util.clean_text(body)
    if not body:
        return None

    rel = path.relative_to(root)
    project = rel.parts[0]
    # layer = directory path between the project folder and the file ('' at root)
    layer = "/".join(rel.parts[1:-1])

    title = meta.get("title") or _title_from_markdown(body, path.stem)
    created = util.to_iso_utc(
        meta.get("date") or meta.get("created") or meta.get("created_at")
    )

    extra = {"file": str(path), "layer": layer}
    extra.update({k: v for k, v in meta.items() if k not in ("title", "layer")})

    return Document(
        source=SOURCE,
        native_id=rel.as_posix(),
        title=title.strip(),
        project=project,
        created_utc=created,
        updated_utc=created,
        ref=str(path),
        extra=extra,
        messages=[Message(seq=0, role="note", text=body, ts_utc=created)],
    )


def ingest(store: Store, root: Optional[Path] = None, force: bool = False) -> IngestReport:
    report = IngestReport(source=SOURCE)
    base = Path(root) if root else config.KNOWLEDGE_DIR
    if not base.exists():
        report.notes.append(f"no knowledge base at {base}")
        return report

    for path in _iter_note_files(base):
        report.scanned += 1
        sig = _signature(path)
        key = path.relative_to(base).as_posix()
        if not force and store.get_signature(SOURCE, key) == sig:
            report.skipped += 1
            continue
        try:
            doc = document_from_note(path, base)
            if doc and store.upsert_document(doc):
                report.changed += 1
            else:
                report.skipped += 1
            store.set_signature(SOURCE, key, sig)
        except Exception as e:
            report.errors.append(f"{path.name}: {e}")

    store.commit()
    return report
