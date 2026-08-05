"""Central paths and constants — the only place a location is defined.

Everything content-bearing lives under the home directory, never in the repo:

    ~/.core/       operating protocol (core.md) + reusable capability
    ~/Knowledge/   project knowledge — and nothing else

``~/Knowledge`` is deliberately visible, and deliberately boring to look at. It
was ``~/.knowledge`` for most of this project's life, and the leading dot hid the
entire knowledge base from Finder — the owner of the content could not browse,
review or trust it without knowing to unhide system folders. A store you cannot
look at is one you cannot verify.

Making it visible then exposed a second problem: the tool's own furniture was
visible too. Opening the folder showed ``_sources``, ``_proposals``, ``_archive``,
``_aliases.json``, ``.index`` and an ``Inbox`` — six things to explain and none of
them the user's knowledge. So the layout is now the shortest true statement of
what this folder is::

    ~/Knowledge/
        acme/           one folder per project. A project's subfolders are its
        gigabite/          layers (meetings/, delivery/, …). Files are indexed
        contoso/           where they sit — no drop box, no filing step.
        README.md          what this folder is, in twenty lines
        .gigabite/         every moving part, hidden: the index, raw imports,
                           archive, proposals, routing aliases

``ls`` shows projects and a README. Everything mechanical is behind the single dot
folder, which is not content and never needs opening.

Content whose project cannot be resolved is written to the knowledge *root* rather
than into a folder invented for it — visible, named after itself, indexed with no
project, and filed the moment it is dragged into a project folder. Guessing a
project is the one failure this design exists to prevent, and hiding the refusal
where nobody sees it is not much better.

Locations can be overridden by environment variable, which is how the tests avoid
touching real content::

    GIGABITE_CORE_DIR         -> ~/.core
    GIGABITE_KNOWLEDGE_DIR    -> ~/Knowledge
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

# --- the machine room (one hidden directory, never browsed) -----------------
# Everything here is either derived from the files in the project folders or is
# an input the user never edits by hand. One dot folder rather than five, so the
# knowledge base reads as knowledge.
MACHINE_DIRNAME = ".gigabite"


def machine_dir(root=None) -> Path:
    """``.gigabite/`` under *root*, defaulting to the current knowledge base.

    The constants below are bound once at import, which is what almost every
    caller wants. This resolves against ``KNOWLEDGE_DIR`` as it stands *now*, for
    the callers that need to follow a knowledge base repointed after import — the
    tests, and any future multi-store use.
    """
    return (Path(root) if root else KNOWLEDGE_DIR) / MACHINE_DIRNAME


def proposals_dir(root=None) -> Path:
    return machine_dir(root) / "proposals"


MACHINE_DIR = machine_dir()

# The SQLite index. Derived data: deletable at any time, rebuilt by `reindex`.
INDEX_DIR = MACHINE_DIR / "index"
DB_PATH = INDEX_DIR / "gigabite.db"

# Raw machine-readable imports — a claude.ai export's conversations.json. Not
# content: every readable form of what they hold lives in a project folder (see
# features.materialize). Kept because re-ingesting them is how the index is
# rebuilt from scratch.
SOURCES_DIR = MACHINE_DIR / "imports"
SOURCES_CLAUDE_AI = SOURCES_DIR / "claude_ai"
SOURCES_GRANOLA = SOURCES_DIR / "granola"

# Decayed knowledge: still indexed and retrievable, just no longer in the way
# (ARCHITECTURE §6).
HISTORICAL_DIR = MACHINE_DIR / "archive"

# Gated synthesis output, listed by `gigabite synthesize --list` (ARCHITECTURE §5).
PROPOSALS_DIR = MACHINE_DIR / "proposals"

# Originals preserved by a migration: the "before" copy of a file that has been
# rewritten into the current layout. Nothing here is indexed; it exists only so a
# migration can be undone by hand.
ORIGINALS_DIR = MACHINE_DIR / "originals"

# Name variants that map onto a project ("gigabyte" -> gigabite). Pure routing
# config, edited rarely, read on every ingest.
ALIASES_FILE = MACHINE_DIR / "aliases.json"

# Older names for the two import paths, kept so an out-of-tree caller keeps working.
INBOX_CLAUDE_AI = SOURCES_CLAUDE_AI
INBOX_GRANOLA = SOURCES_GRANOLA
INBOX_DIR = SOURCES_DIR

# Sentinel project meaning "not resolved". Content saved under it lands at the
# knowledge root: no folder is invented, nothing new appears in Finder, and the
# file is one drag away from being filed. Matched by identity before any
# sanitisation (see features.save._dest_dir), so it can never become a folder
# name however it is spelt.
UNFILED_PROJECT = "(unfiled)"

# The project for content that has no project: a chat about bike suspension, the
# weather, what to watch. It is a real folder rather than a sentinel, because
# "unrouted" and "personal" are different facts and only one of them is worth a
# name — a note the tool could not place is a routing failure to fix, while a
# conversation about a bike is filed exactly where it belongs.
#
# Nothing routes here on its own: its ``_project.md`` carries no keywords, so
# keyword matching can never select it. It is only ever chosen deliberately, by
# ``materialize --include-unfiled`` or by naming it, which is what keeps it from
# becoming the drawer everything ambiguous gets swept into.
PERSONAL_PROJECT = "personal"

# Top-level folder names used by layouts before ``.gigabite/`` existed. Skipped by
# the notes ingester so a store that has not been migrated yet is never indexed
# twice — once from the raw export and once from the folder it sat in.
LEGACY_SYSTEM_DIRNAMES = frozenset({
    "_sources", "_archive", "_proposals", "_needs-triage", "Inbox",
})

CORE_FILE = CORE_DIR / "core.md"

# --- external sources (read-only, never written to) ------------------------
CLAUDE_CODE_PROJECTS_DIR = HOME / ".claude" / "projects"

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
    """Create the local store layout if it does not exist. Safe to call repeatedly.

    Only the machinery is created eagerly. Project folders appear when there is a
    project, so a fresh install shows an empty, unintimidating knowledge base
    rather than a scaffold of folders with nothing in them.
    """
    for d in (
        CORE_DIR,
        KNOWLEDGE_DIR,
        MACHINE_DIR,
        INDEX_DIR,
        SOURCES_DIR,
        SOURCES_CLAUDE_AI,
        SOURCES_GRANOLA,
        HISTORICAL_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
