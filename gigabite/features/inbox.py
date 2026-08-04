"""The drop folder — file anything into the knowledge base with zero AI involved.

Before this, the only inboxes were ``~/Knowledge/_sources/{claude_ai,granola}``:
underscore-prefixed, inside a hidden home directory, and source-specific. Nobody
finds that on a bad day, least of all with no AI credits left. So there is now
one obvious folder — ``Inbox/`` in the repo root — you can drag a file into from
Finder, and the next ``gigabite file`` (or any ingest) puts it where it belongs.

    Inbox/
      README.md                  how to use it, in plain English (committed)
      anything.md                dropped at the root -> project auto-detected
      <project>/file.md          dropped in a project folder -> that project
      <project>/<layer>/file.md  -> that project + layer
      _filed/<YYYY-MM-DD>/       originals after filing (never deleted)
      _needs-triage/             couldn't be routed confidently — never guessed

Two invariants this module must not break:

1. **The routing guarantee** (ROUTING.md / ARCHITECTURE §4). Knowledge is only
   ever persisted through ``features.save.save_note``, which resolves every path
   under ``config.KNOWLEDGE_DIR``. The Inbox is a staging area, never storage.
2. **Nothing is deleted.** Filing *moves* the original into ``_filed/<date>/``,
   de-duplicating the name on collision, so the drop is always recoverable.

Every note written from here carries two provenance fields in its frontmatter:

    origin: inbox/<path as dropped>   where it came from, for auditing
    share:  private                   forward-looking egress marker: this note is
                                      not shareable. Nothing reads it yet — a
                                      future share feature will. Documented here
                                      so the marker exists from day one.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import date
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .. import config, util
# Reuse the frontmatter parser already proven for dropped markdown.
from ..sources.granola import _parse_frontmatter
from . import routing, save as savemod

FILED_DIRNAME = "_filed"
TRIAGE_DIRNAME = "_needs-triage"

TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".vtt"})
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | frozenset({".json"})

# how much of the extracted text feeds the routing probe
PROBE_CHARS = 2000

# A file must sit still this long before it is filed. Filing runs unattended from
# the daily launchd job, so without this a file mid-save (or mid-edit) would be
# filed half-written and then moved out from under the editor. Too-recent files
# are simply left for the next pass.
SETTLE_SECONDS = 60

_SKIP_FILES = {"readme.md"}
_WORDISH = re.compile(r"[-_]+")


def _reserved(name: str) -> bool:
    return name.startswith(("_", "."))


def _iter_drops(root: Path) -> Iterator[Path]:
    """Yield droppable files under *root*, newest-agnostic and deterministic.

    Skips README.md and anything whose path contains a reserved segment, which is
    what keeps ``_filed/`` and ``_needs-triage/`` out of the next pass.
    """
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(_reserved(part) for part in rel.parts):
            continue
        if path.name.lower() in _SKIP_FILES:
            continue
        yield path


# ---------------------------------------------------------------------------
# text extraction (by extension only — never guess at a binary)
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
    """Return ``(text, frontmatter, reason)`` for a dropped file.

    A non-empty *reason* means the file cannot be filed and belongs in
    ``_needs-triage/`` — unknown extension, empty, not UTF-8, or no text left
    after extraction. Extraction is by extension only: we never sniff a binary.
    """
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        kind = suffix or "no extension"
        return "", {}, f"unsupported file type ({kind}) — text can't be extracted"
    try:
        if path.stat().st_size == 0:
            return "", {}, "file is empty"
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "", {}, "not UTF-8 text — can't be read safely"
    except OSError as e:
        return "", {}, f"can't be read ({e.strerror or e})"

    meta: Dict[str, str] = {}
    if suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return "", {}, f"invalid JSON ({e.msg} at line {e.lineno})"
        body = render_json(data)
    else:
        meta, body = _parse_frontmatter(raw)
        if suffix == ".vtt":
            body = strip_vtt(body)

    text = util.clean_text(body)
    if not text:
        return "", meta, "no readable text after extraction"
    return text, meta, ""


# ---------------------------------------------------------------------------
# project / layer resolution
# ---------------------------------------------------------------------------

def forced_context(rel: Path, projects: List[dict]) -> Tuple[Optional[str], Optional[str]]:
    """A drop inside ``<project>/[<layer>/]`` forces that project (and layer).

    The first directory segment is matched case-insensitively against both the
    project's folder name and its ``project:`` meta name, so ``Inbox/Beta Co/``
    and ``Inbox/beta-co/`` both hit the same project.
    """
    dirs = rel.parts[:-1]
    if not dirs:
        return None, None
    head = dirs[0].strip().lower()
    for p in projects:
        names = {Path(p["path"]).name.lower(), str(p.get("name", "")).strip().lower()}
        if head in names:
            return p["name"], (dirs[1] if len(dirs) > 1 else None)
    return None, None


def probe(path: Path, text: str) -> str:
    """The string keyword routing sees: filename as words, then the text head."""
    name = _WORDISH.sub(" ", path.stem)
    return f"{name}\n{text[:PROBE_CHARS]}"


# ---------------------------------------------------------------------------
# moving originals (never deleting them)
# ---------------------------------------------------------------------------

def _unique_file(directory: Path, name: str) -> Path:
    """A non-colliding *name* in *directory* (append -2, -3, … before the suffix).

    Mirrors ``save._unique_path``, but for an arbitrary extension.
    """
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = directory / name
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def _move(path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_file(dest_dir, path.name)
    shutil.move(str(path), str(target))
    return target


# ---------------------------------------------------------------------------
# the filing pass
# ---------------------------------------------------------------------------

def _triage(path: Path, rel: Path, root: Path, reason: str, dry_run: bool) -> dict:
    entry = {"origin": rel.as_posix(), "reason": reason, "moved_to": ""}
    if not dry_run:
        entry["moved_to"] = str(_move(path, root / TRIAGE_DIRNAME))
    return entry


def _file_one(path: Path, root: Path, projects: List[dict], day: str,
              dry_run: bool) -> Tuple[str, dict]:
    """File a single drop. Returns ``('filed'|'triaged', entry)``."""
    rel = path.relative_to(root)
    text, front, reason = extract(path)
    if reason:
        return "triaged", _triage(path, rel, root, reason, dry_run)

    project, layer = forced_context(rel, projects)
    if not project:
        ctx = routing.resolve_context(probe(path, text))
        project, layer = ctx.get("project"), ctx.get("layer")
    if not project:
        return "triaged", _triage(
            path, rel, root,
            "no project matched the filename or contents — move it into an "
            "Inbox/<project>/ folder to force one",
            dry_run,
        )

    entry = {"origin": rel.as_posix(), "project": project, "layer": layer or "",
             "note": "", "filed_to": ""}
    if dry_run:
        return "filed", entry

    # save_note is the ONLY way knowledge is persisted (ROUTING.md).
    note_path = savemod.save_note(
        text, project, layer=layer,
        title=front.get("title") or None,
        ts=front.get("date") or None,
        meta={"origin": f"inbox/{rel.as_posix()}", "share": "private"},
    )
    savemod.ensure_project(project)          # idempotent; covers an @marker project
    entry["note"] = str(note_path)
    entry["filed_to"] = str(_move(path, root / FILED_DIRNAME / day))
    return "filed", entry


def file_inbox(store=None, *, dry_run: bool = False, settle_seconds: int = 0) -> dict:
    """File everything dropped in ``config.INBOX_DROP_DIR``. Returns a report.

        {root, scanned, filed: [...], triaged: [...], waiting: [...],
         errors: [...], dry_run}

    Never raises for a single bad file — the failure is recorded and the pass
    continues, because this runs inside ingest and must not abort it.

    *settle_seconds* leaves files modified more recently than that for the next
    pass (see ``SETTLE_SECONDS``). ``ingest.run`` passes it because it runs
    unattended from launchd and must not grab a half-written file; ``gigabite
    file`` leaves it at 0, since a file you just dropped by hand should file now
    rather than appear to do nothing.

    *store* is accepted for interface symmetry with the ingesters and is
    deliberately unused: filing does not touch the index. ``ingest.run`` calls
    this *before* the note ingester so the note it writes gets indexed in the
    same pass; ``gigabite file`` runs the note ingester itself afterwards.
    """
    root = config.INBOX_DROP_DIR
    report: dict = {"root": str(root), "scanned": 0, "filed": [], "triaged": [],
                    "waiting": [], "errors": [], "dry_run": dry_run}
    if not root.exists():
        return report

    projects = savemod.list_projects()
    day = date.today().isoformat()
    now = time.time()
    for path in _iter_drops(root):
        rel = path.relative_to(root).as_posix()
        if settle_seconds > 0:
            try:
                age = now - path.stat().st_mtime
            except OSError as e:            # vanished mid-pass; nothing to file
                report["errors"].append(f"{rel}: {e}")
                continue
            if age < settle_seconds:
                report["waiting"].append(rel)
                continue
        report["scanned"] += 1
        try:
            kind, entry = _file_one(path, root, projects, day, dry_run)
        except Exception as e:
            report["errors"].append(f"{rel}: {e}")
            continue
        report[kind].append(entry)
    return report
