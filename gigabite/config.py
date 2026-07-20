"""Central paths and constants.

Everything content-bearing lives under the home directory, never in the repo:

    ~/.core/         operating protocol (core.md) + reusable capability
    ~/.knowledge/    project knowledge, ingested content, the search index, inboxes

Locations can be overridden with environment variables (useful for tests):

    GIGABITE_CORE_DIR         -> ~/.core
    GIGABITE_KNOWLEDGE_DIR    -> ~/.knowledge
"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()


def _env_path(var: str, default: Path) -> Path:
    val = os.environ.get(var)
    return Path(val).expanduser() if val else default


# --- top-level stores -------------------------------------------------------
CORE_DIR = _env_path("GIGABITE_CORE_DIR", HOME / ".core")
KNOWLEDGE_DIR = _env_path("GIGABITE_KNOWLEDGE_DIR", HOME / ".knowledge")

# --- within the knowledge base ---------------------------------------------
INDEX_DIR = KNOWLEDGE_DIR / ".index"
DB_PATH = INDEX_DIR / "gigabite.db"

# Drop-file inboxes. Anything placed here is picked up on the next ingest.
INBOX_DIR = KNOWLEDGE_DIR / "_inbox"
INBOX_CLAUDE_AI = INBOX_DIR / "claude_ai"   # Anthropic data export (conversations.json / .zip)
INBOX_GRANOLA = INBOX_DIR / "granola"       # Granola markdown/JSON exports or pasted notes

# Where decayed / historical knowledge would move (reserved; see ARCHITECTURE §6).
HISTORICAL_DIR = KNOWLEDGE_DIR / "_historical"

CORE_FILE = CORE_DIR / "core.md"

# --- external sources (read-only, never written to) ------------------------
CLAUDE_CODE_PROJECTS_DIR = HOME / ".claude" / "projects"
GRANOLA_APP_DIR = HOME / "Library" / "Application Support" / "Granola"

# --- source identifiers -----------------------------------------------------
SOURCE_CLAUDE_CODE = "claude_code"
SOURCE_CLAUDE_AI = "claude_ai"
SOURCE_GRANOLA = "granola"
SOURCE_NOTE = "note"

ALL_SOURCES = (SOURCE_CLAUDE_CODE, SOURCE_CLAUDE_AI, SOURCE_GRANOLA, SOURCE_NOTE)

SOURCE_LABELS = {
    SOURCE_CLAUDE_CODE: "Claude Code",
    SOURCE_CLAUDE_AI: "Claude.ai",
    SOURCE_GRANOLA: "Granola",
    SOURCE_NOTE: "Note",
}


def ensure_dirs() -> None:
    """Create the local store layout if it does not exist. Safe to call repeatedly."""
    for d in (
        CORE_DIR,
        KNOWLEDGE_DIR,
        INDEX_DIR,
        INBOX_DIR,
        INBOX_CLAUDE_AI,
        INBOX_GRANOLA,
        HISTORICAL_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
