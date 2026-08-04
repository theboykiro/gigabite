"""Move an older hidden knowledge base to the visible ``~/Knowledge`` layout.

The knowledge base used to live at ``~/.knowledge``. The leading dot hid it from
Finder, which meant the person whose knowledge it was could not open the folder,
browse it, or check that anything had actually been filed. A store you cannot
look at is a store you cannot trust, so the layout changed:

    ~/.knowledge/            ->  ~/Knowledge/
    ~/.knowledge/_inbox/     ->  ~/Knowledge/_sources/
    ~/.knowledge/_historical/->  ~/Knowledge/_archive/
    <repo>/Inbox/            ->  ~/Knowledge/Inbox/

This module performs that move once, and is safe to run repeatedly afterwards:
anything already in its new place is reported as such and left alone.

Design rules, because this touches the only copy of real content:

  * Nothing is deleted. Directories are *moved*, and a move is refused outright
    if the destination already holds a file of that name.
  * ``plan()`` computes every step without touching the disk, so ``--dry-run``
    shows exactly what will happen.
  * The search index is not migrated. It is derived data and is rebuilt from the
    files by the next ingest, so carrying it across would only risk stale
    absolute paths in the ``ref`` column.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config


@dataclass
class Step:
    """One planned filesystem move, and why it is being made."""
    src: Path
    dst: Path
    what: str
    skip: str = ""          # non-empty means "already done" or "nothing to do"

    @property
    def actionable(self) -> bool:
        return not self.skip


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> list[Step]:
        return [s for s in self.steps if s.actionable]


def _legacy_root() -> Path:
    return Path.home() / ".knowledge"


def plan(
    legacy: Optional[Path] = None,
    target: Optional[Path] = None,
    repo_inbox: Optional[Path] = None,
) -> Plan:
    """Work out every move required, without performing any of them."""
    legacy = Path(legacy) if legacy else _legacy_root()
    target = Path(target) if target else config.KNOWLEDGE_DIR
    repo_inbox = Path(repo_inbox) if repo_inbox is not None else config.REPO_ROOT / "Inbox"

    p = Plan()

    # 1. the knowledge base itself
    if legacy.resolve() == target.resolve():
        p.steps.append(Step(legacy, target, "knowledge base",
                            skip="already at the target path"))
    elif not legacy.exists():
        p.steps.append(Step(legacy, target, "knowledge base",
                            skip="no legacy ~/.knowledge to move"))
    elif target.exists() and any(target.iterdir()):
        # Merging two populated roots is ambiguous; refuse rather than guess.
        p.warnings.append(
            f"both {legacy} and {target} exist and are non-empty — "
            f"merge them by hand, then re-run"
        )
        p.steps.append(Step(legacy, target, "knowledge base",
                            skip="target already populated"))
    else:
        p.steps.append(Step(legacy, target, "knowledge base"))

    # 2. renames within the (possibly just moved) knowledge base
    for old, new, what in (
        ("_inbox", "_sources", "raw exports"),
        ("_historical", "_archive", "decayed knowledge"),
    ):
        src = target / old
        dst = target / new
        # Before the move the folders are still under the legacy root.
        probe = src if src.exists() else legacy / old
        if not probe.exists():
            p.steps.append(Step(src, dst, what, skip=f"no {old}/ to rename"))
        elif dst.exists():
            p.steps.append(Step(src, dst, what, skip=f"{new}/ already exists"))
        else:
            p.steps.append(Step(src, dst, what))

    # 3. the drop folder moves out of the repo
    dst_inbox = target / "Inbox"
    if not repo_inbox.exists():
        p.steps.append(Step(repo_inbox, dst_inbox, "drop folder",
                            skip="no repo Inbox/ to move"))
    else:
        droppings = [q for q in repo_inbox.iterdir() if q.name != "README.md"]
        if not droppings:
            # Only the committed README: the folder carries no content, so the
            # move is a no-op and the README stays in git where it belongs.
            p.steps.append(Step(repo_inbox, dst_inbox, "drop folder",
                                skip="repo Inbox/ holds nothing but its README"))
        else:
            p.steps.append(Step(repo_inbox, dst_inbox, "drop folder contents"))

    return p


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def apply(p: Plan) -> list[str]:
    """Carry out a plan. Returns a line per action taken."""
    done: list[str] = []
    for step in p.steps:
        if not step.actionable:
            continue
        if step.what == "drop folder contents":
            step.dst.mkdir(parents=True, exist_ok=True)
            for item in list(step.src.iterdir()):
                if item.name == "README.md":
                    continue
                dest = step.dst / item.name
                if dest.exists():
                    done.append(f"skipped {item.name}: already in {step.dst}")
                    continue
                _move(item, dest)
                done.append(f"moved {item.name} -> {dest}")
            continue

        if not step.src.exists():
            continue
        _move(step.src, step.dst)
        done.append(f"moved {step.src} -> {step.dst}")
    return done


def describe(p: Plan) -> list[str]:
    """Human-readable rendering of a plan, for --dry-run."""
    out: list[str] = []
    for step in p.steps:
        if step.actionable:
            out.append(f"  will move  {step.src}  ->  {step.dst}   ({step.what})")
        else:
            out.append(f"  skip       {step.what}: {step.skip}")
    for w in p.warnings:
        out.append(f"  WARNING    {w}")
    return out
