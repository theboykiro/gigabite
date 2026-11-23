"""Tidy a project folder: propose where loose files belong, apply only on request.

The intake path (``intake.py``) files what *arrives through gigabite*. Files dropped
straight into a project folder in Finder stay where they landed, and over weeks the
project root fills up. This module looks at the **top level of each project only**
and proposes moves; it never guesses.

Three rules, in order, each of which has to be unambiguous to fire:

  1. **duplicate**   identical bytes to another file in the project. The copy with the
                     human name is kept; the other goes to the tidy trash.
  2. **folder name** the file's name contains the name of exactly one existing
                     subfolder as a whole phrase ("slice-2-..." -> ``Slice 2``).
  3. **note layer**  a markdown note whose frontmatter says ``layer:`` or
                     ``source: meeting``, and the folder for it already exists.

Anything else is reported as unplaced and left alone. Nothing is ever overwritten or
deleted: collisions get a ``-2`` suffix, duplicates move to
``.gigabite/tidy/trash/<run>/``, and every run writes a manifest that ``undo`` replays.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from .. import config, util
from .save import _unique_named, list_projects

# Files that mean something to gigabite itself, or are mid-edit, are never moved.
RESERVED = {"_project.md", "people.md"}
# A file touched this recently may still be being written or open in an app.
QUIET_SECONDS = 10 * 60
# Skip hashing anything bigger than this; a duplicate of a huge file is not worth a read.
MAX_HASH_BYTES = 200 * 1024 * 1024
# A subfolder name shorter than this (normalised) matches too much to be a signal.
MIN_FOLDER_NAME = 4

SOURCE_LAYERS = {"meeting": "meetings"}


@dataclass
class Move:
    project: str
    src: str
    dst: str
    kind: str          # "duplicate" | "folder" | "layer"
    reason: str


@dataclass
class Plan:
    moves: List[Move] = field(default_factory=list)
    unplaced: Dict[str, List[str]] = field(default_factory=dict)  # project -> names
    skipped: List[str] = field(default_factory=list)              # busy/locked, with why

    def as_dict(self) -> dict:
        return {"moves": [asdict(m) for m in self.moves],
                "unplaced": self.unplaced, "skipped": self.skipped}


# --------------------------------------------------------------------------- helpers

def _norm(text: str) -> str:
    """Lowercase, every run of non-alphanumerics -> one space, padded for whole-phrase match."""
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _sha(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > MAX_HASH_BYTES:
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _frontmatter(path: Path) -> dict:
    if path.suffix.lower() != ".md":
        return {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
        meta, _ = util.parse_frontmatter(head)
        return meta or {}
    except OSError:
        return {}


def _humanness(name: str) -> int:
    """Higher = more clearly a name a person gave it (case, spaces) vs a slug."""
    return (any(c.isupper() for c in name)) + (" " in name)


def _files_under(folder: Path) -> List[Path]:
    out = []
    for p in folder.rglob("*"):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(folder).parts):
            out.append(p)
    return out


def _busy(path: Path, siblings: set, now: float) -> Optional[str]:
    if path.name.startswith("~$") or ("~$" + path.name) in siblings:
        return "open in an app (lock file)"
    try:
        if now - path.stat().st_mtime < QUIET_SECONDS:
            return "modified in the last 10 minutes"
    except OSError:
        return "unreadable"
    return None


# --------------------------------------------------------------------------- planning

def plan_project(root: Path, name: str, now: Optional[float] = None) -> Plan:
    now = time.time() if now is None else now
    plan = Plan()
    subdirs = {d.name: _norm(d.name) for d in root.iterdir()
               if d.is_dir() and not d.name.startswith(("_", "."))
               and len(_norm(d.name).strip()) >= MIN_FOLDER_NAME}
    top = sorted(p for p in root.iterdir()
                 if p.is_file() and not p.name.startswith((".", "_"))
                 and p.name not in RESERVED)
    names = {p.name for p in top}

    # Hash everything in the project once, grouped by size so unique sizes never get read.
    everything = _files_under(root)
    by_size: Dict[int, List[Path]] = {}
    for p in everything:
        try:
            by_size.setdefault(p.stat().st_size, []).append(p)
        except OSError:
            pass
    digest: Dict[Path, str] = {}
    groups: Dict[str, List[Path]] = {}
    for size, paths in by_size.items():
        if size == 0 or len(paths) < 2:
            continue
        for p in paths:
            d = _sha(p)
            if d:
                digest[p] = d
                groups.setdefault(d, []).append(p)

    handled = set()
    for p in top:
        if p.name.startswith("~$"):      # the lock file itself: not content, not worth reporting
            continue
        why_busy = _busy(p, names, now)
        if why_busy:
            plan.skipped.append(f"{name}/{p.name}: {why_busy}")
            continue

        # 1. duplicate -------------------------------------------------------
        twins = [q for q in groups.get(digest.get(p, ""), []) if q != p]
        if twins and p not in handled:
            nested = [q for q in twins if q.parent != root]
            if nested:
                drop = True            # a copy already lives in a subfolder: it is the filed one
                keeper = nested[0]
            else:
                rivals = [p] + twins
                keeper = max(rivals, key=lambda q: (_humanness(q.name), -q.stat().st_mtime))
                drop = keeper != p
                if drop:
                    handled.add(p)
            if drop:
                run = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
                dst = config.machine_dir() / "tidy" / "trash" / run / name / p.name
                plan.moves.append(Move(name, str(p), str(dst), "duplicate",
                                       f"identical to {keeper.relative_to(root)}"))
                continue

        # 2. folder name -----------------------------------------------------
        stem = _norm(p.stem)
        hits = {d: n for d, n in subdirs.items() if n in stem}
        if len(hits) == 1:           # two different folders named in one file = ambiguous: leave it
            folder = next(iter(hits))
            plan.moves.append(Move(name, str(p), str(root / folder / p.name),
                                   "folder", f"name contains '{folder}'"))
            continue

        # 3. note layer ------------------------------------------------------
        meta = _frontmatter(p)
        layer = (meta.get("layer") or "").strip() or SOURCE_LAYERS.get(
            (meta.get("source") or "").strip(), "")
        if layer and (root / layer).is_dir():
            plan.moves.append(Move(name, str(p), str(root / layer / p.name),
                                   "layer", f"note says {layer}"))
            continue

        plan.unplaced.setdefault(name, []).append(p.name)
    return plan


def plan_all(only: Optional[str] = None, now: Optional[float] = None) -> Plan:
    total = Plan()
    for proj in list_projects():
        if only and proj["name"] != only:
            continue
        sub = plan_project(Path(proj["path"]), proj["name"], now)
        total.moves += sub.moves
        total.skipped += sub.skipped
        for k, v in sub.unplaced.items():
            total.unplaced.setdefault(k, []).extend(v)
    return total


# --------------------------------------------------------------------------- apply / undo

def _tidy_dir() -> Path:
    return config.machine_dir() / "tidy"


def apply(plan: Plan, now: Optional[float] = None) -> Optional[Path]:
    """Perform the moves. Returns the manifest path (None if there was nothing to do)."""
    if not plan.moves:
        return None
    done = []
    for m in plan.moves:
        src = Path(m.src)
        if not src.is_file():
            continue
        dst_dir = Path(m.dst).parent
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = _unique_named(dst_dir, Path(m.dst).name)
        shutil.move(str(src), str(dst))
        done.append({**asdict(m), "dst": str(dst)})
    if not done:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    _tidy_dir().mkdir(parents=True, exist_ok=True)
    manifest = _tidy_dir() / f"{stamp}.json"
    manifest.write_text(json.dumps({"moves": done}, indent=2), encoding="utf-8")
    return manifest


def undo_last() -> tuple:
    """Reverse the most recent run. Returns (manifest, restored, skipped)."""
    runs = sorted(_tidy_dir().glob("*.json")) if _tidy_dir().is_dir() else []
    if not runs:
        return None, 0, 0
    manifest = runs[-1]
    moves = json.loads(manifest.read_text(encoding="utf-8"))["moves"]
    restored = skipped = 0
    for m in reversed(moves):
        dst, src = Path(m["dst"]), Path(m["src"])
        if not dst.is_file():
            skipped += 1
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(_unique_named(src.parent, src.name)))
        restored += 1
    manifest.rename(manifest.with_suffix(".undone"))
    return manifest, restored, skipped
