"""Save knowledge into the central store — never into the working directory.

This module is the *only* correct way to persist content or project meta. It
always resolves paths under ``config.KNOWLEDGE_DIR`` (``~/Knowledge``),
independent of the caller's current working directory. That is the rule from
ARCHITECTURE §4: code goes to the working dir, knowledge goes to the fixed
central store. Route knowledge writes here and they can never be misfiled into
whatever repo Claude Code happens to be pointed at.

Two writers, because there are two kinds of content:

  * ``save_note(text, …)``  — text becomes a markdown note with frontmatter.
  * ``save_file(src, …)``   — an existing file (a screenshot, a PDF, a .vtt) is
    copied in byte-for-byte under the same ``{project}/{layer}/`` rule.

``save_file`` exists because refusing a file you cannot extract text from means
losing it. A screenshot pasted into a conversation is content; the honest
handling is to keep the file and index only what is actually known about it
(name, date, size, type), which is what ``sources.notes`` does. Guessing at its
contents would be worse than admitting they are unread.

Layout produced (ARCHITECTURE §2.2):

    ~/Knowledge/
      {project}/
        _project.md              per-project meta (keywords, layers, background)
        {layer}/                 nested context layer
          {YYYY-MM-DD-slug}.md   a saved note
          screenshot.png         a stored file, kept as-is
        {YYYY-MM-DD-slug}.md     a note with no layer -> project root
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .. import config, util

# ---------------------------------------------------------------------------
# name / slug safety
# ---------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SPACE = re.compile(r"[\s_]+", re.UNICODE)
_SLUG_DASH = re.compile(r"-+")


def slugify(text: str) -> str:
    """Filesystem-safe slug: lowercase, punctuation dropped, spaces -> '-'.

    Returns '' when nothing usable remains (callers pick a fallback).
    """
    s = (text or "").strip().lower()
    s = _SLUG_STRIP.sub("", s)      # drop anything that isn't word/space/hyphen
    s = _SLUG_SPACE.sub("-", s)     # runs of space/underscore -> single hyphen
    s = _SLUG_DASH.sub("-", s).strip("-")
    return s


def _safe_folder(name: str, kind: str = "name") -> str:
    """Sanitise a project/layer into a single safe folder name.

    Guards against path traversal: any '/', '\\' or '..' is neutralised because
    slugify strips separators and dots outright. Raises if nothing survives.
    """
    slug = slugify(name)
    if not slug:
        raise ValueError(f"invalid {kind}: {name!r}")
    return slug


# ---------------------------------------------------------------------------
# project meta
# ---------------------------------------------------------------------------

def _project_meta(project: str, keywords: List[str], layers: List[str]) -> str:
    """Render a filled _project.md (mirrors install/scaffold/_project.md structure)."""
    return (
        "---\n"
        f"project: {project}\n"
        f"keywords: {', '.join(keywords)}\n"
        f"layers: {', '.join(layers)}\n"
        "---\n\n"
        f"# {project}\n\n"
        "## Background\n"
        "_What this project is, why it exists, current phase._\n\n"
        "## Stakeholders\n"
        "_Key people, their roles, who decides what._\n\n"
        "## Vocabulary\n"
        "_Project-specific terms, acronyms, shorthand — the words that signal "
        "this context._\n\n"
        "## Notes\n"
        "_Anything else that helps route and load the right context._\n"
    )


def ensure_project(
    project: str,
    keywords: Optional[List[str]] = None,
    layers: Optional[List[str]] = None,
) -> Path:
    """Create ``~/Knowledge/{project}/`` and a _project.md if absent.

    Idempotent: an existing _project.md is left untouched. Returns the project dir.
    """
    folder = _safe_folder(project, "project")
    proj_dir = config.KNOWLEDGE_DIR / folder
    proj_dir.mkdir(parents=True, exist_ok=True)

    meta_path = proj_dir / "_project.md"
    if not meta_path.exists():
        meta_path.write_text(
            _project_meta(project, keywords or [], layers or []),
            encoding="utf-8",
        )
    return proj_dir


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------

def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


_MAX_SLUG_LEN = 60


def _truncate_slug(slug: str, max_len: int = _MAX_SLUG_LEN) -> str:
    """Cap a slug so ``{date}-{slug}.md`` never exceeds filesystem name limits.

    Without this, a title-less save of a long paragraph (the whole text becomes
    the slug via ``_first_line``) can produce a 200+ char filename and fail with
    ``OSError: File name too long``. Cuts at the last hyphen inside the limit so
    words aren't chopped mid-word.
    """
    if len(slug) <= max_len:
        return slug
    truncated = slug[:max_len].rstrip("-")
    if "-" in truncated:
        truncated = truncated.rsplit("-", 1)[0]
    return truncated or slug[:max_len]


def _unique_path(directory: Path, base: str) -> Path:
    """A non-colliding ``{base}.md`` in *directory* (append -2, -3, … if taken)."""
    candidate = directory / f"{base}.md"
    n = 2
    while candidate.exists():
        candidate = directory / f"{base}-{n}.md"
        n += 1
    return candidate


def _dest_dir(project: str, layer: Optional[str]) -> tuple:
    """Resolve ``{project}/[{layer}/]`` under the knowledge base.

    Returns ``(directory, layer_name)``. This is the single point where a project
    and layer become a path, shared by ``save_note`` and ``save_file``, so both
    writers are covered by the same guarantee and the same sanitisation.

    ``config.UNFILED_PROJECT`` means the project was not resolved, and resolves to
    the knowledge root: the file is visible, named after itself, and one drag away
    from being filed, without a folder being invented to hold the tool's
    uncertainty. It is matched *before* sanitisation, because ``slugify`` would
    turn it into an ordinary project folder — exactly the misfiling it exists to
    prevent. It takes no layer: nothing about unrouted content is known well
    enough to nest it.
    """
    if project == config.UNFILED_PROJECT:
        return config.KNOWLEDGE_DIR, ""
    dest = config.KNOWLEDGE_DIR / _safe_folder(project, "project")
    layer_name = ""
    if layer:
        layer_name = _safe_folder(layer, "layer")
        dest = dest / layer_name
    return dest, layer_name


_FM_RESERVED = ("title", "date", "project", "layer")


def _extra_frontmatter(meta: Optional[Dict[str, object]]) -> str:
    """Render caller-supplied frontmatter lines (provenance, egress markers, …).

    Reserved keys (title/date/project/layer) are dropped so extras can never
    rewrite the fields the notes ingester routes on. Values are forced onto one
    line — a stray newline would otherwise close the frontmatter block early.
    """
    lines: List[str] = []
    for key, value in (meta or {}).items():
        slug = slugify(str(key)).replace("-", "_")
        if not slug or slug in _FM_RESERVED or value in (None, ""):
            continue
        flat = " ".join(str(value).split())
        lines.append(f"{slug}: {flat}\n")
    return "".join(lines)


def save_note(
    text: str,
    project: str,
    layer: Optional[str] = None,
    title: Optional[str] = None,
    ts: Optional[str] = None,
    meta: Optional[Dict[str, object]] = None,
) -> Path:
    """Write a markdown note under ``~/Knowledge/{project}/[{layer}/]``.

    The path is ALWAYS resolved under ``config.KNOWLEDGE_DIR``, regardless of the
    caller's working directory — this is the routing guarantee (ARCHITECTURE §4).
    Creates directories as needed and returns the written file path.

    *meta* adds extra frontmatter fields after the standard four (e.g.
    ``origin:`` provenance, ``share:`` egress marker). Optional and additive —
    omit it and the note is byte-identical to before.

    Pass ``project=config.UNFILED_PROJECT`` when the project genuinely is not
    known; see ``_dest_dir``.
    """
    dest, layer_name = _dest_dir(project, layer)
    dest.mkdir(parents=True, exist_ok=True)

    created = util.to_iso_utc(ts) if ts else datetime.now(timezone.utc).isoformat()
    date = util.short_date(created)

    heading = title or _first_line(text) or "note"
    slug = _truncate_slug(slugify(heading) or "note")
    path = _unique_path(dest, f"{date}-{slug}")

    body = util.clean_text(text)
    content = (
        "---\n"
        f"title: {heading}\n"
        f"date: {date}\n"
        f"project: {'' if project == config.UNFILED_PROJECT else project}\n"
        f"layer: {layer_name}\n"
        f"{_extra_frontmatter(meta)}"
        "---\n\n"
        f"{body}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# files that are not text (screenshots, PDFs, anything at all)
# ---------------------------------------------------------------------------

def _unique_named(directory: Path, name: str) -> Path:
    """A non-colliding *name* in *directory* (append -2, -3, … before the suffix).

    Mirrors ``_unique_path`` but preserves an arbitrary extension. Nothing in
    this module ever overwrites an existing file.
    """
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = directory / name
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def save_file(
    src: Path,
    project: str,
    layer: Optional[str] = None,
    name: Optional[str] = None,
    move: bool = False,
) -> Path:
    """Store *src* under ``~/Knowledge/{project}/[{layer}/]`` unchanged.

    Same routing guarantee as ``save_note``: the destination is always resolved
    under ``config.KNOWLEDGE_DIR`` from the sanitised project and layer, never
    from the caller's working directory. The bytes are copied verbatim — this is
    for content that is not text, so there is nothing to render.

    Never overwrites: a colliding name gets a ``-2`` suffix. *move* relocates the
    original instead of copying it, which is only for a file already inside the
    knowledge base; content arriving from outside is copied so the caller's copy
    survives a mistake.
    """
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"not a file: {src}")

    dest_dir, _ = _dest_dir(project, layer)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # The stored name is sanitised the same way a note's is, so a filename can no
    # more escape the knowledge base than a project name can.
    raw = name or src.name
    stem = slugify(Path(raw).stem) or "file"
    dest = _unique_named(dest_dir, f"{stem}{Path(raw).suffix.lower()}")

    if move:
        shutil.move(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))
    return dest


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def _split_csv(value: str) -> List[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def list_projects() -> List[dict]:
    """Scan ``~/Knowledge`` for ``{project}/_project.md`` and parse their meta.

    Returns ``[{name, keywords: [...], layers: [...], path}]``, sorted by name.
    Reserved top-level folders (leading '_' or '.') are skipped.
    """
    root = config.KNOWLEDGE_DIR
    out: List[dict] = []
    if not root.exists():
        return out

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        meta_path = entry / "_project.md"
        if not meta_path.exists():
            continue
        meta, _ = util.parse_frontmatter(
            meta_path.read_text(encoding="utf-8", errors="replace")
        )
        out.append({
            "name": meta.get("project") or entry.name,
            "keywords": _split_csv(meta.get("keywords", "")),
            "layers": _split_csv(meta.get("layers", "")),
            "path": str(entry),
        })
    return out
