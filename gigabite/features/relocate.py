"""Bring an older knowledge base up to the current layout.

Three migrations live here, all one-way and all safe to re-run.

**The knowledge base became visible.** It used to live at ``~/.knowledge``, and the
leading dot hid it from Finder, which meant the person whose knowledge it was could
not open the folder, browse it, or check that anything had actually been filed. A
store you cannot look at is a store you cannot trust::

    ~/.knowledge/  ->  ~/Knowledge/

**The machinery went behind one dot folder.** Making the base visible made the
tool's own furniture visible with it: opening ``~/Knowledge`` showed ``_sources``,
``_proposals``, ``_archive``, ``_aliases.json`` and ``.index`` before it showed a
single project. None of that is knowledge, and every one of them was a thing to
explain. They all move into ``.gigabite/``::

    _sources/ or _inbox/    ->  .gigabite/imports/
    _archive/ or _historical/ -> .gigabite/archive/
    _proposals/             ->  .gigabite/proposals/
    _aliases.json           ->  .gigabite/aliases.json
    .index/                 ->  .gigabite/index/

**The drop folder was retired.** ``Inbox/`` was a staging area: content was dropped
there and a filing pass later moved it into a project, which gave content two homes
and made "where is my meeting?" depend on whether the pass had run. The staging
step is gone — ``~/Knowledge`` is the drop surface itself — and whatever is left in
the old folder comes back into the store::

    Inbox/_filed/**        ->  .gigabite/originals/inbox/   (already filed; kept)
    Inbox/<anything else>  ->  ~/Knowledge/                 (never filed; you place it)

What is left afterwards is project folders and a README.

Design rules, because this touches the only copy of real content:

  * **Nothing that holds content is deleted.** Files are *moved*, and a move is
    skipped outright if the destination already holds a file of that name. The
    only deletions are empty directories and the README files that documented the
    folders being retired — they describe a layout that no longer exists, and
    leaving them is how someone ends up following instructions to a dead folder.
  * ``plan()`` computes every step without touching the disk, so ``--dry-run``
    shows exactly what will happen.
  * The search index is rebuilt afterwards rather than migrated. It is derived
    data, and it holds absolute paths in its ``ref`` column that this migration
    invalidates.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config

# READMEs that documented a retired folder. Deleted rather than carried along:
# they describe a workflow that no longer exists, and a stale instruction is worse
# than no instruction. Everything else is moved.
_OBSOLETE_READMES = frozenset({"readme.md"})


@dataclass
class Step:
    """One planned filesystem action, and why it is being made."""
    src: Path
    dst: Optional[Path]
    what: str
    skip: str = ""          # non-empty means "already done" or "nothing to do"
    delete: bool = False    # True for an obsolete doc or an emptied directory

    @property
    def actionable(self) -> bool:
        return not self.skip


@dataclass
class Plan:
    steps: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # Directories the pruning pass must never remove, however empty they end up.
    protect: set = field(default_factory=set)
    # Retired folders to attempt to remove once their contents are out. Listed
    # explicitly because a folder can need removing without this migration having
    # moved anything out of it — an `_archive/` that was already empty, or an
    # `Inbox/<project>/` subfolder whose one file was filed long ago.
    prune_roots: set = field(default_factory=set)

    @property
    def actionable(self) -> list:
        return [s for s in self.steps if s.actionable]


def _legacy_root() -> Path:
    return Path.home() / ".knowledge"


def _machine(target: Path, path: Path) -> Path:
    """A machinery path from ``config``, re-rooted onto *target*.

    Every machinery location is defined once, in ``config``, as a path under
    ``config.KNOWLEDGE_DIR``. Re-rooting rather than reading ``config`` directly is
    what lets this migration run against any tree — a test fixture, a copy taken
    before a risky run — without a stray step reaching into the real store.
    """
    return target / path.relative_to(config.KNOWLEDGE_DIR)


def plan(
    legacy: Optional[Path] = None,
    target: Optional[Path] = None,
    repo_inbox: Optional[Path] = None,
) -> Plan:
    """Work out every action required, without performing any of them."""
    legacy = Path(legacy) if legacy else _legacy_root()
    target = Path(target) if target else config.KNOWLEDGE_DIR
    repo_inbox = Path(repo_inbox) if repo_inbox is not None else config.REPO_ROOT / "Inbox"

    p = Plan()
    p.protect = {legacy, target, _machine(target, config.MACHINE_DIR), repo_inbox}
    _plan_root(p, legacy, target)
    _plan_machinery(p, legacy, target)
    for drop in _drop_folders(target, repo_inbox):
        _plan_drop(p, drop, target)
        p.prune_roots.add(drop)
    if not any(s.what.startswith("drop") for s in p.steps):
        p.steps.append(Step(target / "Inbox", None, "drop folder",
                            skip="no Inbox/ left to retire"))
    return p


def _plan_root(p: Plan, legacy: Path, target: Path) -> None:
    """Step 1: the knowledge base itself."""
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


def _plan_machinery(p: Plan, legacy: Path, target: Path) -> None:
    """Step 2: every non-project entry moves into ``.gigabite/``.

    Both the pre-visible names (``_inbox``, ``_historical``) and the visible-era
    ones (``_sources``, ``_archive``) are handled, so a store can be two versions
    behind and still arrive in one run.
    """
    moves = (
        ("_inbox", config.SOURCES_DIR, "raw imports"),
        ("_sources", config.SOURCES_DIR, "raw imports"),
        ("_historical", config.HISTORICAL_DIR, "archive"),
        ("_archive", config.HISTORICAL_DIR, "archive"),
        ("_proposals", config.PROPOSALS_DIR, "proposals"),
        ("_aliases.json", config.ALIASES_FILE, "routing aliases"),
        (".index", config.INDEX_DIR, "search index"),
    )
    moves = tuple((name, _machine(target, dest), what) for name, dest, what in moves)
    for name, dest, what in moves:
        # Before the root move the entry is still under the legacy root.
        src = target / name
        probe = src if src.exists() else legacy / name
        if probe.is_dir():
            p.prune_roots.add(src)
        if not probe.exists():
            p.steps.append(Step(src, dest, what, skip=f"no {name} to move"))
            continue
        if probe.is_dir():
            p.steps.extend(_plan_dir_contents(probe, src, dest, what))
        else:
            p.steps.append(Step(src, dest, what,
                                skip="already in place" if dest.exists() else ""))


def _plan_dir_contents(probe: Path, src: Path, dest: Path, what: str) -> list:
    """Move a legacy directory's files one by one into *dest*.

    Per-file rather than moving the directory itself, because the destination may
    already exist (a partial earlier run, or ``ensure_dirs`` having created it) and
    ``shutil.move`` would then nest the old folder inside the new one.
    """
    steps: list = []
    for path in sorted(probe.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(probe)
        if path.name.lower() in _OBSOLETE_READMES or path.name.startswith("_replaced-"):
            steps.append(Step(src / rel, None, f"{what} README", delete=True))
            continue
        target_file = dest / rel
        steps.append(Step(src / rel, target_file, what,
                          skip="already in place" if target_file.exists() else ""))
    if not steps:
        steps.append(Step(src, dest, what, skip=f"{probe} is empty"))
    return steps


def _drop_folders(target: Path, repo_inbox: Path) -> list:
    """Every place an old drop folder could still be sitting."""
    seen: list = []
    for candidate in (target / "Inbox", repo_inbox):
        try:
            if candidate.is_dir() and not any(c.samefile(candidate) for c in seen):
                seen.append(candidate)
        except OSError:
            continue
    return seen


def _plan_drop(p: Plan, drop: Path, target: Path) -> None:
    """Step 3: retire one drop folder, file by file.

    Per-file because the two halves go to different places: what was already filed
    is a duplicate of a note that exists and must not be indexed again, while what
    was never filed still needs a home the user can see.
    """
    found = False
    for path in sorted(drop.rglob("*")):
        if not path.is_file():
            continue
        found = True
        rel = path.relative_to(drop)
        if rel.parts[0] == "_filed":
            dst: Optional[Path] = _machine(target, config.ORIGINALS_DIR) / "inbox" / rel
            what = "drop folder original"
        elif path.name.lower() in _OBSOLETE_READMES or path.name.startswith("_"):
            if drop in p.protect and drop != target / "Inbox":
                # A committed README in the repo's own folder is not ours to remove.
                p.steps.append(Step(path, None, "drop folder README",
                                    skip="committed in the repo; left alone"))
            else:
                p.steps.append(Step(path, None, "drop folder README", delete=True))
            continue
        else:
            # Never filed, so no project was ever resolved for it. It goes to the
            # knowledge root, where it is visible and indexed with no project.
            dst = target / rel.name
            what = "drop folder content"
        skip = "already at the target path" if dst and dst.exists() else ""
        p.steps.append(Step(path, dst, what, skip=skip))
    if not found:
        p.steps.append(Step(drop, None, "drop folder", skip=f"{drop} is empty"))


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------

def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def apply(p: Plan) -> list:
    """Carry out a plan. Returns a line per action taken."""
    done: list = []
    emptied: set = set()
    protect = {q.resolve() for q in p.protect if q.exists()}
    # Every folder a retired root contains is a removal candidate, whether or not
    # a file came out of it on this run.
    for root in p.prune_roots:
        if root.is_dir():
            emptied.add(root)
            emptied.update(q for q in root.rglob("*") if q.is_dir())
    for step in p.steps:
        if not step.actionable or not step.src.exists():
            continue
        if step.dst is None and not step.delete:
            continue                     # a reporting-only step
        if step.delete:
            step.src.unlink()
            emptied.add(step.src.parent)
            done.append(f"removed {step.src} (documented a folder that no longer exists)")
            continue
        if step.dst is None:
            continue
        emptied.add(step.src.parent)
        _move(step.src, step.dst)
        done.append(f"moved {step.src} -> {step.dst}")
    emptied = {q for q in emptied if q.exists()}

    done.extend(_prune(emptied, protect))
    return done


def _prune(directories: set, protect: set) -> list:
    """Remove directories emptied by the migration, deepest first. rmdir only.

    ``rmdir`` refuses a non-empty directory, which is the safety property that
    matters: anything this migration did not account for keeps its folder and is
    reported rather than swept away. *protect* additionally pins the roots — the
    knowledge base itself must survive being emptied.
    """
    out: list = []
    for directory in sorted(directories, key=lambda q: len(q.parts), reverse=True):
        current = directory
        while current.exists() and current.resolve() not in protect:
            try:
                current.rmdir()
            except OSError:
                break
            out.append(f"removed the emptied folder {current}")
            current = current.parent
    return out


def describe(p: Plan) -> list:
    """Human-readable rendering of a plan, for --dry-run."""
    out: list = []
    for step in p.steps:
        if not step.actionable:
            out.append(f"  skip       {step.what}: {step.skip}")
        elif step.delete:
            out.append(f"  will drop  {step.src}   ({step.what}, now obsolete)")
        else:
            out.append(f"  will move  {step.src}  ->  {step.dst}   ({step.what})")
    for w in p.warnings:
        out.append(f"  WARNING    {w}")
    return out
