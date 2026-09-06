"""Index the knowledge base — every file in it, where the user put it.

``~/Knowledge`` is the store *and* the drop surface. There is no staging folder
and no filing step: a file placed anywhere under a project folder is indexed in
place by the next ingest, and moving it later moves its project with it, because
the folder *is* the metadata.

What counts as content — everything, with four narrow exceptions:

  - anything starting with '.', which is where every moving part lives:
    ``.gigabite/`` holds the index, raw imports, the archive and the proposals,
    and none of it is content (also catches ``.DS_Store`` and editor droppings);
  - the folder names an older layout used for the same machinery
    (``config.LEGACY_SYSTEM_DIRNAMES``), so a store that has not been migrated
    yet is not indexed twice;
  - files starting with '_' and ``README.md`` — meta, not knowledge
    (``_project.md`` is the project's own metadata);
  - directories starting with '_' *below* the top level.

Loose files at the knowledge root are indexed with an empty project. That is the
honest record for content whose project could not be resolved: it is real and
searchable, and what is unknown about it is where it belongs. Drag it into a
project folder and the next ingest picks the project up from the folder.

Two things this module does that a plain file indexer would not:

**Non-text files are kept and indexed by their metadata.** A screenshot is
content. It is indexed by filename, type, size and date, with an explicit note
that its contents were not read. There is no OCR and no inference — the index
says what is known and no more.

**A file may declare that it *is* an existing document.** ``features.materialize``
renders a claude.ai chat or a meeting as readable markdown under a project
folder, and stamps the source document's id into its frontmatter::

    doc_id: claude_ai:c28ac56fc34f9c87
    source: claude_ai

Without that, the rendering would be indexed as a second, separate document with
the same words, and every search would return the conversation twice. With it,
the file resolves to the document it renders. If that document is already in the
index under its own source, this ingester steps aside; if it is not — the export
it came from has been removed, or the index was rebuilt from files alone — the
file *becomes* the document, so the readable copy is sufficient on its own and
nothing is lost by deleting a raw export.

Each ordinary file becomes a Document with source='note', native_id = its path
relative to the knowledge root, project = the top folder, and the layer in extra.
Incremental via an ``mtime:size`` file signature under the 'note' source key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from .. import config, util
from ..store import Document, Message, Store
from . import IngestReport

# config.py owns the canonical SOURCE_NOTE constant + label; use the literal here
# so this module stands alone if imported before that wiring lands.
SOURCE = "note"

_SKIP_FILES = {"readme.md"}


def _skip_file(name: str) -> bool:
    """Meta and machine droppings: '_project.md', '.DS_Store', 'README.md'."""
    return name.startswith((".", "_")) or name.lower() in _SKIP_FILES


def _skip_dir(name: str, top_level: bool) -> bool:
    if name.startswith("."):
        return True
    if top_level:
        return name in config.LEGACY_SYSTEM_DIRNAMES
    return name.startswith("_")


def _iter_content_files(root: Path) -> Iterator[Path]:
    """Yield every indexable file under *root*, deepest-last and deterministic."""
    def walk(directory: Path, top_level: bool) -> Iterator[Path]:
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if not _skip_dir(entry.name, top_level):
                    yield from walk(entry, False)
            elif entry.is_file() and not _skip_file(entry.name):
                yield entry

    yield from walk(root, True)


def _signature(path: Path) -> str:
    st = path.stat()
    return f"{int(st.st_mtime)}:{st.st_size}"


def _context(rel: Path) -> tuple:
    """``(project, layer)`` from a path relative to the knowledge root.

    The folder is the metadata. A file at the knowledge root has no project —
    recorded as empty rather than guessed at — and picks one up the moment it is
    dragged into a project folder.
    """
    parts = rel.parts
    if len(parts) == 1:                       # loose file at the knowledge root
        return "", ""
    project = "" if parts[0].startswith("_") else parts[0]
    return project, "/".join(parts[1:-1])


# ---------------------------------------------------------------------------
# building documents
# ---------------------------------------------------------------------------

def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}"
        size /= 1024.0
    return f"{size:.0f} GB"


def _binary_body(path: Path, rel: Path, stored: str) -> str:
    """What can honestly be said about a file whose contents were not read."""
    try:
        size = _human_size(path.stat().st_size)
    except OSError:
        size = "unknown size"
    return "\n".join((
        f"{path.name}",
        "",
        f"type:   {path.suffix.lower() or 'no extension'}",
        f"size:   {size}",
        f"stored: {util.short_date(stored)}",
        f"file:   {rel.as_posix()}",
        "",
        "Stored file. Its contents have not been read: gigabite indexes a "
        "non-text file by its name, type and date, and does not guess at what "
        "is inside it. Open the file to see it.",
    ))


def document_from_note(path: Path, root: Path) -> Optional[Document]:
    """A Document for one file in the knowledge base, or None if there is nothing.

    Markdown/text is indexed as prose. Anything else is indexed by its metadata
    (see ``_binary_body``) rather than skipped. A file whose frontmatter declares
    a ``doc_id:`` resolves to *that* document instead of a new one.
    """
    rel = path.relative_to(root)
    project, layer = _context(rel)
    suffix = path.suffix.lower()

    meta: dict = {}
    body = ""
    if suffix in (".md", ".markdown", ".txt"):
        raw = path.read_text(encoding="utf-8", errors="replace")
        meta, parsed = util.parse_frontmatter(raw)
        body = util.clean_text(parsed)

    if body:
        title = meta.get("title") or util.title_from_markdown(body, path.stem)
        created = util.to_iso_utc(
            meta.get("date") or meta.get("created") or meta.get("created_at")
        )
        role = "note"
    else:
        # Not text, or text with nothing in it. Keep the file, index the facts.
        created = util.to_iso_utc(path.stat().st_mtime)
        title = meta.get("title") or path.stem
        body = _binary_body(path, rel, created)
        role = "file"

    extra = {"file": str(path), "layer": layer}
    extra.update({k: v for k, v in meta.items()
                  if k not in ("title", "layer", "doc_id")})

    # A rendering of an already-indexed conversation declares which one it is.
    declared = (meta.get("doc_id") or "").strip()
    source = SOURCE
    if declared:
        # 'source:' records what the original was (meeting, claude_ai, …) so the
        # adopted row still reports itself honestly in search. Fall back to the
        # prefix of the declared id, which is namespaced by source.
        #
        # Through ``canonical_source`` because these values were written by the
        # version of the tool that wrote the file: everything filed before the
        # 'granola' source was renamed says ``source: granola``, and will for as
        # long as the file exists. The alternative was rewriting the user's own
        # notes to match an internal rename (config.LEGACY_SOURCE_IDS).
        source = config.canonical_source(
            (meta.get("source") or declared.split(":", 1)[0] or SOURCE).strip())

    return Document(
        source=source,
        native_id=rel.as_posix(),
        title=title.strip(),
        project=project,
        created_utc=created,
        updated_utc=created,
        ref=str(path),
        extra=extra,
        messages=[Message(seq=0, role=role, text=body, ts_utc=created)],
        doc_id_override=declared,
    )


def ingest(store: Store, root: Optional[Path] = None, force: bool = False) -> IngestReport:
    report = IngestReport(source=SOURCE)
    base = Path(root) if root else config.KNOWLEDGE_DIR
    if not base.exists():
        report.notes.append(f"no knowledge base at {base}")
        return report

    for path in _iter_content_files(base):
        report.scanned += 1
        sig = _signature(path)
        key = path.relative_to(base).as_posix()
        if not force and store.get_signature(SOURCE, key) == sig:
            report.skipped += 1
            continue
        try:
            doc = document_from_note(path, base)
            if doc is None:
                report.skipped += 1
            elif _defers_to_owner(store, doc, path):
                # The document this file renders is already indexed from the
                # export it came from. Indexing the file too would duplicate it.
                report.skipped += 1
            elif store.upsert_document(doc):
                report.changed += 1
            else:
                report.skipped += 1
            store.set_signature(SOURCE, key, sig)
        except Exception as e:
            report.errors.append(f"{path.name}: {e}")

    store.commit()
    return report


def _defers_to_owner(store: Store, doc: Document, path: Path) -> bool:
    """True when *doc* renders a document another file already owns.

    Only applies to a file that declared a ``doc_id:``. The owner is identified by
    the ``ref`` recorded on the existing row: when it points somewhere other than
    this file, that other file (a raw export) is the source of truth and this one
    is a readable copy, so it must not be indexed again. When it points *here*, or
    when there is no row at all, this file is the document — so edits to a
    materialized file still reach the index, and a rendering whose export has gone
    away still carries its own content.
    """
    if not doc.doc_id_override:
        return False
    ref = store.document_ref(doc.doc_id)
    return ref is not None and ref != str(path)
