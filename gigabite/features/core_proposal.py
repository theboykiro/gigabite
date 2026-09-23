"""Core setup: the gated applier behind `/core-setup`.

`core.md` is the constitutional layer — loaded in full, every session. The
`/core-setup` interview (docs/CORE_SETUP.md) collects answers slot by slot in
Claude Code, and this module is the half that decides what reaches disk.

`apply_proposal` is the only function here permitted to write `core.md`, and it
cannot do so without an explicit `approved` mapping handed to it by a caller
acting on user approval. Approval is **per slot**: `approved` carries only the
slots the user accepted — the rest render as they were (`[FILL]` for the stated
ones), which is a visible gap rather than an invented answer.

**Set aside, never destroy.** If a `core.md` already exists, it is moved to a
dated copy under `config.ORIGINALS_DIR` before the new one is written — the same
pattern `refresh_doc` in `install.sh` uses when it replaces the knowledge README.
That happens unconditionally and there is no parameter to disable it. Nothing
here overwrites in place and nothing here deletes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .. import config, util
from . import core_slots


def _originals_dir() -> Path:
    # Late-resolving, so a repointed knowledge base is honoured. This is the
    # installer's set-aside directory.
    return config.ORIGINALS_DIR


def _core_file() -> Path:
    return config.CORE_FILE


def set_aside(core_path: Path) -> Path | None:
    """Move an existing `core.md` to a dated copy and return where it went.

    Mirrors `refresh_doc` in `install.sh`: `replaced-YYYY-MM-DD-core.md` under the
    machinery's originals directory, so the replaced copy is kept without
    appearing in the knowledge base as a stray file. Never overwrites an earlier
    set-aside copy — a second apply on the same day gets its own suffix.
    """
    if not core_path.exists():
        return None
    kept_dir = _originals_dir()
    kept_dir.mkdir(parents=True, exist_ok=True)
    date = util.short_date(datetime.now(timezone.utc).isoformat())
    stem = f"replaced-{date}-{core_path.name}"
    kept = kept_dir / stem
    n = 2
    while kept.exists():
        kept = kept_dir / f"replaced-{date}-{n}-{core_path.name}"
        n += 1
    core_path.replace(kept)
    return kept


def apply_proposal(approved: Mapping[str, str], *, core_path=None) -> Path:
    """Write `core.md` from the slots the user approved. The only writer here.

    `approved` maps slot_id -> the final markdown body for that slot and must be
    supplied explicitly by a caller acting on user approval. There is no default,
    no inference and no "apply everything" path: a slot absent from the mapping
    is rendered as it was, which for a stated slot means `[FILL]`.

    An empty mapping is a no-op — it returns the path without touching the file,
    so "the user approved nothing" can never truncate an existing protocol.

    An existing file is *always* set aside as a dated copy before the new one is
    written. There is no flag to skip it: the user's protocol is not something a
    caller may decide is not worth keeping, and the one time it matters is the
    one time someone would have turned it off. Nothing is overwritten in place
    and nothing is deleted.
    """
    target = Path(core_path) if core_path is not None else _core_file()

    if not approved:
        return target

    body = core_slots.render_core_md(dict(approved))

    set_aside(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target
