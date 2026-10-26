"""Index Claude Code's auto-memory: ``~/.claude/projects/*/memory/*.md``.

Each memory file is one fact Claude was told to keep — frontmatter with ``name``,
``description`` and a ``metadata:`` block, then a body. They sit next to the
session transcripts but are not transcripts, and until now nothing read them, so
a fact saved to memory was recalled only in sessions whose folder loaded that
memory directory, and never by search.

**The project comes from the note's words, not its folder.** The memory directory
is named after the working directory the session ran in, and a fact about a
client's fiscal calendar saved in a session run inside a tool's repo is still
about the client. So the project is resolved the way every other intake resolves
one (``intake.route``, the routing ``gigabite save`` and materialize use) over
the name, description and body. When nothing resolves, the note is indexed with
no project — searchable, and honest about what is unknown. The folder name is
never consulted.

``MEMORY.md`` is skipped: it is the index of the other files, one line each, and
indexing it would return every fact a second time in a worse shape.

Each file becomes a Document with source='note' (it is a note, and ``note`` is
also what materialize treats as already a file, so nothing is ever copied into
the knowledge base). Nothing is written anywhere but the index. Incremental via
an ``mtime:size`` signature under this module's own sync key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from .. import config, util
from ..features import intake
from ..store import Document, Message, Store
from . import IngestReport

# Sync bookkeeping key. Kept apart from 'note', whose keys are paths relative to
# the knowledge root, so neither scanner can read the other's state.
SYNC_KEY = "claude_memory"

_INDEX_FILE = "memory.md"


def _iter_memory_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.glob("*/memory/*.md")):
        if path.is_file() and path.name.lower() != _INDEX_FILE:
            yield path


def _signature(path: Path) -> str:
    st = path.stat()
    return f"{int(st.st_mtime)}:{st.st_size}"


def document_from_memory(path: Path, root: Path) -> Optional[Document]:
    """A Document for one memory file, or None if it has no body."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, parsed = util.parse_frontmatter(raw)
    body = util.clean_text(parsed)
    if not body:
        return None

    name = meta.get("name") or path.stem
    description = meta.get("description") or ""
    # chars=None: a memory is short and the whole of it is evidence.
    project, _ = intake.route(name, f"{description}\n{body}", chars=None)

    created = util.to_iso_utc(meta.get("modified") or path.stat().st_mtime)
    text = f"{description}\n\n{body}" if description else body
    rel = path.relative_to(root)
    return Document(
        source=config.SOURCE_NOTE,
        native_id=f"claude-memory/{rel.as_posix()}",
        # The name, as words: it is what the memory index and the user call the
        # fact, so a search for it should find it by title.
        title=" ".join(name.replace("_", "-").split("-")),
        project=project or "",
        created_utc=created,
        updated_utc=created,
        ref=str(path),
        extra={"file": str(path), "memory_name": name,
               "memory_type": meta.get("type") or ""},
        messages=[Message(seq=0, role="note", text=text, ts_utc=created)],
    )


def ingest(store: Store, projects_dir: Optional[Path] = None,
           force: bool = False) -> IngestReport:
    report = IngestReport(source=config.SOURCE_NOTE)
    root = Path(projects_dir) if projects_dir else config.CLAUDE_CODE_PROJECTS_DIR
    if not root.exists():
        return report

    for path in _iter_memory_files(root):
        report.scanned += 1
        try:
            sig = _signature(path)
            key = str(path)
            if not force and store.get_signature(SYNC_KEY, key) == sig:
                report.skipped += 1
                continue
            doc = document_from_memory(path, root)
            if doc is not None and store.upsert_document(doc):
                report.changed += 1
            else:
                report.skipped += 1
            store.set_signature(SYNC_KEY, key, sig)
        except Exception as e:  # keep going; report per-file failures
            report.errors.append(f"{path.name}: {e}")

    store.commit()
    return report
