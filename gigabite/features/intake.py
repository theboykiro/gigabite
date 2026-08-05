"""Getting content into the knowledge base — text, transcripts, screenshots, anything.

This module replaces the old drop folder (``features.inbox``). The drop folder was
a staging area: you put a file in ``~/Knowledge/Inbox``, and a filing pass later
moved it into a project. That meant content had two possible homes and the answer
to "where is my meeting?" depended on whether the pass had run yet — a second
store wearing the first one's clothes.

There is now one destination. ``~/Knowledge/{project}/[{layer}/]`` is where
content lives, and it is also where you put it: a file placed anywhere in the
knowledge base is indexed in place by the next ingest (``sources.notes``), with
nothing to run and nothing to remember. What survives from the drop folder is the
part that was actually load-bearing:

  * **text extraction** — .md / .txt / .vtt / .json are read for their prose, so a
    subtitle file or a JSON blob indexes as language rather than as syntax.
  * **never guess a project** — routing either resolves a project from an explicit
    ``@marker`` or the ``keywords:`` in each ``_project.md``, or it does not, in
    which case the content is written to the knowledge root untouched. A confidently
    misfiled note is worse than an unfiled one.
  * **never delete, never overwrite** — a colliding name gets a ``-2`` suffix, and
    content arriving from outside the store is copied rather than moved.

And one thing the drop folder got wrong: a file whose text could not be extracted
used to be refused and shunted aside. Screenshots are content. A file is now
always kept — ``save_file`` stores the bytes and ``sources.notes`` indexes what is
honestly available (name, date, size, type) without pretending to have read it.
There is no OCR here and no inference about an image's contents.

Everything written from here carries provenance frontmatter:

    origin: <where it came from>       for auditing
    share:  private                    forward-looking egress marker: not shareable.
                                       Nothing reads it yet; the marker exists from
                                       day one so it never has to be backfilled.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import config, util
# Reuse the frontmatter parser already proven for markdown notes.
from ..sources.granola import _parse_frontmatter
from . import routing, save as savemod

# Extensions we can read prose out of. Anything else is kept as a file and
# indexed by its metadata — never discarded.
TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".vtt"})
READABLE_SUFFIXES = TEXT_SUFFIXES | frozenset({".json"})

# how much of the extracted text feeds the routing probe
PROBE_CHARS = 2000

_WORDISH = re.compile(r"[-_]+")

# extract() outcomes
TEXT = "text"
BINARY = "binary"


# ---------------------------------------------------------------------------
# text extraction (by extension only — never sniff a binary)
# ---------------------------------------------------------------------------

_VTT_META = ("WEBVTT", "NOTE", "STYLE", "REGION")


def strip_vtt(text: str) -> str:
    """Spoken text out of a .vtt subtitle file: drop headers, cues, timestamps."""
    kept: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or "-->" in s or s.isdigit():
            continue
        if s.upper().startswith(_VTT_META):
            continue
        kept.append(s)
    return "\n".join(kept)


def render_json(node, prefix: str = "") -> str:
    """Flatten JSON into ``key: value`` lines so it indexes as prose, not a blob."""
    lines: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}{key}"
            if isinstance(value, (dict, list)):
                lines.append(render_json(value, f"{path}."))
            else:
                lines.append(f"{path}: {value}")
    elif isinstance(node, list):
        for i, value in enumerate(node, 1):
            if isinstance(value, (dict, list)):
                lines.append(render_json(value, f"{prefix}{i}."))
            else:
                lines.append(f"{prefix}{i}: {value}")
    else:
        return str(node)
    return "\n".join(line for line in lines if line.strip())


def extract(path: Path) -> Tuple[str, Dict[str, str], str]:
    """Return ``(text, frontmatter, kind)`` for a file.

    *kind* is ``TEXT`` when prose was extracted and ``BINARY`` when it was not —
    an unknown extension, an empty file, bytes that are not UTF-8, malformed
    JSON, or nothing left after extraction. ``BINARY`` is not a failure and never
    a reason to refuse the file; it means "index this by its metadata, because its
    contents cannot be read honestly". Extraction is by extension only: we never
    sniff a binary hoping to find words in it.
    """
    suffix = path.suffix.lower()
    if suffix not in READABLE_SUFFIXES:
        return "", {}, BINARY
    try:
        if path.stat().st_size == 0:
            return "", {}, BINARY
        raw = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return "", {}, BINARY

    meta: Dict[str, str] = {}
    if suffix == ".json":
        try:
            body = render_json(json.loads(raw))
        except json.JSONDecodeError:
            return "", {}, BINARY
    else:
        meta, body = _parse_frontmatter(raw)
        if suffix == ".vtt":
            body = strip_vtt(body)

    text = util.clean_text(body)
    if not text:
        return "", meta, BINARY
    return text, meta, TEXT


# ---------------------------------------------------------------------------
# project / layer resolution — never guessed
# ---------------------------------------------------------------------------

def probe(name: str, text: str = "", chars: Optional[int] = PROBE_CHARS) -> str:
    """The string keyword routing sees: the name as words, then the body.

    The filename carries real signal ("acme-traffic-drop.md" routes on its own)
    so it goes first. *chars* caps how much body follows it; ``None`` uses all of
    it.

    The cap is there for a file being skimmed on its way in, where reading a
    100MB drop to route it would be silly. It is the wrong default when the whole
    text is already in hand: a 7,000-word meeting transcript can easily not name
    its client until the fifth minute, and truncating at 2,000 characters made
    six real client meetings look unroutable. Callers that hold the full document
    pass ``chars=None`` — more text is more evidence, and routing scores by how
    many distinct keywords match, so a passing mention cannot outvote a document
    that is genuinely about something else.
    """
    body = text if chars is None else text[:chars]
    return f"{_WORDISH.sub(' ', Path(name).stem)}\n{body}"


def route(name: str, text: str = "",
          chars: Optional[int] = PROBE_CHARS) -> Tuple[Optional[str], Optional[str]]:
    """Resolve ``(project, layer)`` for incoming content, or ``(None, None)``.

    ``None`` means the project is genuinely unresolved, and the caller must leave
    it unfiled rather than pick something plausible.
    """
    # accept_unknown_marker=False: this is a document, not something typed. An
    # '@handle' inside a transcript must never conjure a project folder.
    ctx = routing.resolve_context(probe(name, text, chars),
                                  accept_unknown_marker=False)
    return ctx.get("project"), ctx.get("layer")


# ---------------------------------------------------------------------------
# placing content (the only way in)
# ---------------------------------------------------------------------------

def _resolved(project: str, layer: str, name: str, text: str) -> Tuple[str, str, bool]:
    """``(project, layer, unfiled)`` — a forced project wins, else route, else unfiled."""
    project = (project or "").strip()
    layer = (layer or "").strip()
    if project:
        return project, layer, False
    detected, detected_layer = route(name, text)
    if detected:
        return detected, layer or (detected_layer or ""), False
    return config.UNFILED_PROJECT, "", True


def place_text(
    text: str,
    *,
    title: str = "",
    day: str = "",
    project: str = "",
    layer: str = "",
    source: str = "",
    origin: str = "",
    doc_id: str = "",
) -> Tuple[Path, str, bool]:
    """Write handed-over *text* straight into the store. Returns ``(path, project, triaged)``.

    This is the single entry point for content that arrives as text rather than as
    a file: ``gigabite paste``, ``/granola``, a transcript pasted into a session.
    It writes through ``save.save_note``, so the routing guarantee holds however
    the text got here.

    *project* forces a project; left empty it is detected, and an undetectable one
    is written to the knowledge root. *doc_id* declares that this text is a rendering of
    a document already in the index, which is what keeps it from being indexed a
    second time (see ``features.materialize`` and ``sources.notes``).
    """
    title = " ".join((title or "").split())
    day = day or date.today().isoformat()
    project, layer, triaged = _resolved(project, layer, title or "note", text)

    if not triaged:
        savemod.ensure_project(project)   # idempotent; covers a brand-new project
    path = savemod.save_note(
        text, project, layer=layer or None, title=title or None, ts=day,
        meta={"origin": origin or "pasted", "share": "private",
              "source": source, "doc_id": doc_id},
    )
    return path, project, triaged


def place_file(
    src: Path,
    *,
    project: str = "",
    layer: str = "",
    move: bool = False,
) -> Tuple[Path, str, bool]:
    """Store an existing file in the knowledge base. Returns ``(path, project, triaged)``.

    Works for anything: a markdown note, a .vtt, a screenshot, a PDF. Text files
    are read only to route them — the file itself is stored byte-for-byte, so what
    lands in the project folder is what you handed over, not a rewrite of it.

    A file whose text cannot be extracted is still stored. It routes on its
    filename alone, and if that resolves nothing it is written to the knowledge root
    where it is visible and indexed rather than lost.
    """
    src = Path(src)
    text, _meta, _kind = extract(src)
    project, layer, triaged = _resolved(project, layer, src.name, text)
    if not triaged:
        savemod.ensure_project(project)
    path = savemod.save_file(src, project, layer=layer or None, move=move)
    return path, project, triaged
