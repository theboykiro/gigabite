"""Write every indexed document out as a readable file in the knowledge base.

The problem this solves: the index knew about content the knowledge base could not
show you. A claude.ai chat lived inside ``conversations.json`` and inside SQLite,
and nowhere else — so ``~/Knowledge`` in Finder was a partial view of the store
while claiming to be all of it. "It's in there, search for it" is not the same as
"open the folder and read it", and only one of those survives the tool.

``materialize`` closes that gap. Each document that exists only in the index is
rendered as markdown and saved under ``~/Knowledge/{project}/[{layer}/]`` in the
same shape as every other note, so the folder becomes the complete picture.

Three things make it safe to run against real content:

**Nothing is guessed.** The project comes from the document's own recorded project
when it has one, otherwise from the same keyword routing every other intake uses,
and otherwise from nowhere — the document is left in the index and skipped, unless
``--include-unfiled`` says to write it under ``personal/`` (``config.PERSONAL_PROJECT``),
which is where a conversation with no working context actually belongs.

**Nothing is duplicated.** This is the whole difficulty. A rendering written into a
project folder is a markdown file in the knowledge base, so the notes ingester
would index it as a *second* document with the same words as the first, and every
search would return the conversation twice. Each rendering therefore carries the
id of the document it renders::

    doc_id: claude_ai:c28ac56fc34f9c87
    source: claude_ai

and ``sources.notes`` resolves that file to that document rather than to a new one.

**Nothing is destroyed, and a second run does nothing.** Raw exports are left
alone. Already-materialized documents are recognised by scanning the knowledge
base for those ``doc_id:`` stamps — the files themselves are the record, so
idempotency survives an index rebuild — and are skipped.

The rendering is deterministic: the same document produces byte-identical output
every time, so re-running after a crash cannot leave two near-identical files.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .. import config, util
from ..sources.granola import _parse_frontmatter
from . import intake, save as savemod

# Where a materialized document lands when its own source implies a layer.
#
# Meetings already live in ``<project>/meetings/`` — 60-odd of them, filed by hand
# before this existed — so a materialized Granola meeting joins them rather than
# starting a second convention. Chats get their own layer for the same reason a
# transcript is not a note: they are raw material, and mixing them into the
# project root would bury the handful of written notes under dozens of them.
#
# This is the one place the tool picks a layer rather than leaving it empty.
# Override it per run with ``--layer``.
DEFAULT_LAYERS: Dict[str, str] = {
    config.SOURCE_GRANOLA: "meetings",
    config.SOURCE_CLAUDE_AI: "conversations",
    config.SOURCE_CLAUDE_CODE: "conversations",
}

# Documents that are already files. 'note' is the source every file in the
# knowledge base is indexed under, so those need nothing done to them.
ALREADY_FILES = frozenset({config.SOURCE_NOTE})

_ROLE_LABELS = {
    "user": "user",
    "assistant": "assistant",
    "attachment": "attachment",
    "note": "note",
    "file": "file",
}


@dataclass
class Item:
    """One document considered for materialization."""
    doc_id: str
    source: str
    title: str
    date: str
    project: str = ""
    layer: str = ""
    triaged: bool = False
    skip: str = ""              # non-empty means nothing will be written
    path: Optional[Path] = None  # set by apply()

    @property
    def actionable(self) -> bool:
        return not self.skip


@dataclass
class Plan:
    items: List[Item] = field(default_factory=list)

    @property
    def actionable(self) -> List[Item]:
        return [i for i in self.items if i.actionable]

    @property
    def skipped(self) -> List[Item]:
        return [i for i in self.items if not i.actionable]


# ---------------------------------------------------------------------------
# what is already materialized
# ---------------------------------------------------------------------------

def materialized_doc_ids(root: Optional[Path] = None) -> Dict[str, Path]:
    """``{doc_id: path}`` for every file in the knowledge base that stamps one.

    Read from the files rather than from the index on purpose: the files are what
    persists. An index rebuild, a `reindex`, or a machine move must not make this
    command think it has work to do and write a second copy of everything.
    """
    base = Path(root) if root else config.KNOWLEDGE_DIR
    out: Dict[str, Path] = {}
    if not base.exists():
        return out
    for path in sorted(base.rglob("*.md")):
        if config.MACHINE_DIRNAME in path.parts:
            continue
        try:
            meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8",
                                                        errors="replace"))
        except OSError:
            continue
        declared = (meta.get("doc_id") or "").strip()
        if declared and declared not in out:
            out[declared] = path
    return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(doc: dict) -> str:
    """A readable, deterministic markdown body for an indexed document.

    Speaker-labelled turns in their original order, under a one-line provenance
    header. Nothing is summarised, reordered or dropped — the point is that the
    file can stand in for the conversation.
    """
    label = config.SOURCE_LABELS.get(doc.get("source", ""), doc.get("source", ""))
    messages = [m for m in doc.get("messages", []) if (m.get("text") or "").strip()]

    header = " · ".join(p for p in (
        label,
        util.short_date(doc.get("created_utc") or ""),
        f"{len(messages)} turn{'s' if len(messages) != 1 else ''}",
        doc.get("doc_id", ""),
    ) if p and p != "—")

    blocks = [f"_{header}_"]
    # A single-message document (a meeting transcript, a note) is not a dialogue;
    # labelling its one turn would add a heading and no information.
    single = len(messages) == 1
    for m in messages:
        text = util.clean_text(m.get("text"))
        if single:
            blocks.append(text)
            continue
        who = _ROLE_LABELS.get(m.get("role", ""), m.get("role") or "unknown")
        stamp = (m.get("ts_utc") or "")[:16].replace("T", " ")
        blocks.append(f"**{who}**" + (f" · {stamp}" if stamp else ""))
        blocks.append(text)

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

def _known_projects() -> set:
    return {p["name"].lower() for p in savemod.list_projects()}


UNRESOLVED = ("no project resolved — re-run with --project to place these, "
              f"or --include-unfiled to file them under {config.PERSONAL_PROJECT}/")

# Layers ``personal/`` is described as holding, so its _project.md matches the
# shape materialize actually writes into it.
PERSONAL_LAYERS = sorted(set(DEFAULT_LAYERS.values()))


def plan(store, *, source: Optional[str] = None, project: Optional[str] = None,
         layer: Optional[str] = None, limit: Optional[int] = None,
         include_unfiled: bool = False) -> Plan:
    """Decide what would be written, touching nothing.

    Documents are considered oldest first so a run is reproducible and a ``--limit``
    always takes the same slice.

    *project* forces one for the whole run: the tool still never guesses, but you
    can assert what you know ("these ten imports are all client meetings") in one
    command. Without it, a document whose project cannot be resolved is skipped
    rather than written, unless *include_unfiled* says to file it under
    ``personal/``. Skipping by default is deliberate: a document nothing can place
    is usually a routing gap worth seeing, and the flag is how you say "no, these
    really have no project" — a statement about the content, not a fallback the
    tool should reach for on its own.
    """
    done = materialized_doc_ids()
    projects = _known_projects()
    p = Plan()

    docs = sorted(store.iter_documents(include_historical=True),
                  key=lambda d: (d.get("created_utc") or "", d.get("doc_id") or ""))
    written = 0
    for row in docs:
        doc_id = row["doc_id"]
        src = row.get("source") or ""
        if source and src != source:
            continue
        title = (row.get("title") or "").strip()
        item = Item(doc_id=doc_id, source=src, title=title or "(untitled)",
                    date=util.short_date(row.get("created_utc") or ""))

        if src in ALREADY_FILES or _is_already_a_file(row):
            item.skip = "already a file in the knowledge base"
        elif doc_id in done:
            item.skip = f"already materialized → {done[doc_id]}"
        elif limit is not None and written >= limit:
            item.skip = f"beyond --limit {limit}"
        else:
            item.project, item.triaged = _project_for(store, row, title, project,
                                                      projects)
            if item.triaged and not include_unfiled:
                item.skip = UNRESOLVED
            else:
                if item.triaged:
                    item.project = config.PERSONAL_PROJECT
                item.layer = (layer if layer is not None
                              else DEFAULT_LAYERS.get(src, ""))
                written += 1

        p.items.append(item)
    return p


def _is_already_a_file(row: dict) -> bool:
    """True when this document's ``ref`` is a readable file in the knowledge base.

    Belt as well as braces. The ``source`` column says how a document *entered* the
    system, not where it lives, and it is not a reliable test on its own: content
    filed from a project folder reports ``note``, but a document adopted from a
    materialized rendering keeps reporting the source it came from. Materializing
    such a document again would write a second file with the same words — the exact
    duplication this module exists to avoid — so ownership is settled by where the
    file actually is, which cannot drift.
    """
    ref = (row.get("ref") or "").strip()
    if not ref:
        return False
    try:
        path = Path(ref)
        rel = path.relative_to(config.KNOWLEDGE_DIR)
    except ValueError:
        return False                       # outside the store: a raw import or a URL
    return config.MACHINE_DIRNAME not in rel.parts and path.exists()


def _project_for(store, row: dict, title: str, forced: Optional[str],
                 projects: set) -> tuple:
    """``(project, unresolved)`` for one document, without ever guessing.

    In order: a project asserted for the run, then the document's own recorded
    project, then keyword routing over its text. The recorded project is a fact
    from the source rather than an inference — but it is only trusted when a folder
    of that name actually exists, so a stale label cannot conjure a project.
    """
    if forced:
        return forced, False
    recorded = (row.get("project") or "").strip()
    if recorded and recorded.lower() in projects:
        return recorded, False
    full = store.get_document(row["doc_id"]) or {}
    # chars=None: the whole document, not a 2,000-character head. We already hold
    # the full text, and a transcript often names its client well past that point.
    detected, _ = intake.route(title, render(full), chars=None)
    if detected:
        return detected, False
    return config.UNFILED_PROJECT, True


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------

def apply(store, p: Plan) -> List[Item]:
    """Write every actionable item. Returns the items written, with their paths."""
    written: List[Item] = []
    for item in p.actionable:
        doc = store.get_document(item.doc_id)
        if not doc:                       # vanished between plan and apply
            item.skip = "document is no longer in the index"
            continue
        body = render(doc)
        if not body.strip():
            item.skip = "document has no text to write"
            continue

        # save_note is the only way knowledge is persisted (ROUTING.md). The
        # doc_id/source pair is what stops this file being indexed as a second
        # copy of the conversation it renders (see sources.notes).
        savemod.ensure_project(
            item.project,
            # personal/ is described by the layers materialize writes into it, and
            # by no keywords at all, so nothing is ever routed there implicitly.
            layers=PERSONAL_LAYERS if item.project == config.PERSONAL_PROJECT else None,
        )
        item.path = savemod.save_note(
            body, item.project,
            layer=item.layer or None,
            title=item.title if item.title != "(untitled)" else None,
            ts=doc.get("created_utc") or None,
            meta={
                "doc_id": item.doc_id,
                "source": item.source,
                "origin": f"materialized from {doc.get('ref') or item.source}",
                "share": "private",
            },
        )
        # The rendering will not be re-indexed (sources.notes defers to whichever
        # file owns the document), so the row is told where it now lives. Without
        # this, `search --project personal` misses files sitting in personal/.
        store.set_document_project(item.doc_id, item.project)
        written.append(item)
    return written


# ---------------------------------------------------------------------------
# retiring the raw originals that have become readable files
# ---------------------------------------------------------------------------

def retire_sources(store, *, dry_run: bool = False) -> List[dict]:
    """Move raw exports out of ``_sources/`` once their content is a readable file.

    ``_sources/`` is for machine-readable originals, but ten Granola meetings had
    been dropped there as plain markdown before project folders were the norm, so
    real content was sitting in a folder nobody browses. Once ``materialize`` has
    written that content into ``<project>/meetings/``, the export is redundant.

    It is **moved, not deleted** — into ``_archive/_originals/`` — so the migration
    is reversible by hand, and the document is repointed at the readable file so it
    keeps its id, its history, and its place in search.

    Only exports that back exactly one document are retired. A claude.ai
    ``conversations.json`` holds dozens, and re-ingesting it is the only way to
    rebuild them, so it stays exactly where it is.

    Returns one entry per file considered, each ``{path, doc_id, moved_to, skip}``.
    """
    done = materialized_doc_ids()
    out: List[dict] = []
    root = config.SOURCES_DIR
    if not root.exists():
        return out

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".md", ".txt"):
            continue
        if path.name.startswith((".", "_")) or path.name.lower() == "readme.md":
            continue
        entry = {"path": str(path), "doc_id": "", "moved_to": "", "skip": ""}
        rows = store.documents_by_ref(str(path))
        if not rows:
            entry["skip"] = "not indexed — nothing has replaced it yet"
        elif len(rows) > 1:
            entry["skip"] = f"backs {len(rows)} documents; left in place"
        else:
            doc_id = rows[0]["doc_id"]
            entry["doc_id"] = doc_id
            target = done.get(doc_id)
            if not target:
                entry["skip"] = "not materialized yet — run materialize first"
            else:
                # Mirror the import tree under originals/imports/, rather than the
                # path relative to the knowledge root — that would nest the whole
                # ``.gigabite/`` prefix inside ``.gigabite/originals/``.
                dest = (config.ORIGINALS_DIR / "imports"
                        / path.relative_to(config.SOURCES_DIR))
                entry["moved_to"] = str(dest)
                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        entry["skip"] = "an original of that name is already kept"
                        entry["moved_to"] = ""
                    else:
                        shutil.move(str(path), str(dest))
                        # The readable file is now the document's home, so the
                        # notes ingester owns it rather than deferring to a raw
                        # export that is no longer there.
                        store.set_document_ref(doc_id, str(target))
        out.append(entry)
    return out


def run(store, *, source: Optional[str] = None, project: Optional[str] = None,
        layer: Optional[str] = None, limit: Optional[int] = None,
        dry_run: bool = False, retire: bool = True,
        include_unfiled: bool = False) -> tuple:
    """Plan and (unless *dry_run*) apply. Returns ``(plan, retired)``."""
    p = plan(store, source=source, project=project, layer=layer, limit=limit,
             include_unfiled=include_unfiled)
    if not dry_run:
        apply(store, p)
    retired = retire_sources(store, dry_run=dry_run) if retire else []
    return p, retired
