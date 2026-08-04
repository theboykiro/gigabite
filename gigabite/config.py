"""Central paths and constants.

Everything content-bearing lives under the home directory, never in the repo:

    ~/.core/       operating protocol (core.md) + reusable capability
    ~/Knowledge/   project knowledge, ingested content, the search index, inboxes

``~/Knowledge`` is deliberately visible. It was ``~/.knowledge`` for most of this
project's life, and the leading dot hid the entire knowledge base from Finder —
the owner of the content could not browse, review or trust it without knowing to
unhide system folders. A store you cannot look at is one you cannot verify, so
the dot is gone. It also means the drop folder no longer has to live in the repo
to be findable: it is now ``~/Knowledge/Inbox``, next to everything it feeds.

Layout::

    ~/Knowledge/
        README.md          how the whole thing is organised
        Inbox/             drop files here; filed on the next `gigabite file`
        <project>/         one folder per project, optionally split into layers
        _sources/          raw exports, machine-readable rather than browsable
        _archive/          decayed knowledge kept for retrieval, not for reading
        _proposals/        synthesis output awaiting approval
        .index/            the SQLite index (derived; rebuildable at any time)

Folders whose name starts with '_' or '.' are reserved and are never treated as
projects. Locations can be overridden with environment variables, which is how
the tests avoid touching real content::

    GIGABITE_CORE_DIR         -> ~/.core
    GIGABITE_KNOWLEDGE_DIR    -> ~/Knowledge
    GIGABITE_INBOX_DROP_DIR   -> ~/Knowledge/Inbox
"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_path(var: str, default: Path) -> Path:
    val = os.environ.get(var)
    return Path(val).expanduser() if val else default


# --- top-level stores -------------------------------------------------------
CORE_DIR = _env_path("GIGABITE_CORE_DIR", HOME / ".core")
KNOWLEDGE_DIR = _env_path("GIGABITE_KNOWLEDGE_DIR", HOME / "Knowledge")

# --- within the knowledge base ---------------------------------------------
INDEX_DIR = KNOWLEDGE_DIR / ".index"
DB_PATH = INDEX_DIR / "gigabite.db"

# Raw exports: the machine-readable originals a source was ingested from. Kept
# out of the project folders because they are not meant to be read by a human —
# separating them is what lets every remaining top-level folder be a project.
SOURCES_DIR = KNOWLEDGE_DIR / "_sources"
INBOX_CLAUDE_AI = SOURCES_DIR / "claude_ai"  # Anthropic data export (conversations.json / .zip)
INBOX_GRANOLA = SOURCES_DIR / "granola"      # Granola exports not yet filed to a project

# Retained for anything still referring to the old name for the raw-export area.
INBOX_DIR = SOURCES_DIR

# --- the drop folder (staging only, never storage) --------------------------
# One obvious place to drop meeting notes and documents, with no AI involved.
# `gigabite file` (and every ingest) routes what lands here into the project
# folders via features.save.save_note, then moves the original into
# Inbox/_filed/<date>/. It sits inside the knowledge base rather than in the
# repo, so that dropping a client file can never stage it into git.
INBOX_DROP_DIR = _env_path("GIGABITE_INBOX_DROP_DIR", KNOWLEDGE_DIR / "Inbox")

# Where decayed knowledge moves: still indexed and retrievable, just no longer
# in the way (see ARCHITECTURE §6).
HISTORICAL_DIR = KNOWLEDGE_DIR / "_archive"

CORE_FILE = CORE_DIR / "core.md"

# --- external sources (read-only, never written to) ------------------------
CLAUDE_CODE_PROJECTS_DIR = HOME / ".claude" / "projects"
GRANOLA_APP_DIR = HOME / "Library" / "Application Support" / "Granola"

# --- source identifiers -----------------------------------------------------
SOURCE_CLAUDE_CODE = "claude_code"
SOURCE_CLAUDE_AI = "claude_ai"
SOURCE_GRANOLA = "granola"
SOURCE_NOTE = "note"
SOURCE_CALENDAR = "calendar"

ALL_SOURCES = (SOURCE_CLAUDE_CODE, SOURCE_CLAUDE_AI, SOURCE_GRANOLA, SOURCE_NOTE, SOURCE_CALENDAR)

SOURCE_LABELS = {
    SOURCE_CLAUDE_CODE: "Claude Code",
    SOURCE_CLAUDE_AI: "Claude.ai",
    SOURCE_GRANOLA: "Granola",
    SOURCE_NOTE: "Note",
    SOURCE_CALENDAR: "Calendar",
}


def ensure_dirs() -> None:
    """Create the local store layout if it does not exist. Safe to call repeatedly."""
    for d in (
        CORE_DIR,
        KNOWLEDGE_DIR,
        INDEX_DIR,
        SOURCES_DIR,
        INBOX_CLAUDE_AI,
        INBOX_GRANOLA,
        INBOX_DROP_DIR,
        HISTORICAL_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
