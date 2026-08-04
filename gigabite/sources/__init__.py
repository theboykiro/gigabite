"""Source ingesters. Each module exposes:

    ingest(store, **opts) -> IngestReport

and yields gigabite.store.Document objects the store upserts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .. import config


def project_alias(project: str) -> str:
    """Map a source's own project label onto your canonical short name.

    Aliases live in ``~/.knowledge/_aliases.json``, e.g.::

        {"Acme Product Manager": "acme", "giga-bite": "gigabite"}

    Every source that infers a project from something outside your control must
    go through here, or the same project ends up under two names and scoping a
    search to one silently hides the other. claude.ai supplies a long project
    label; Claude Code supplies a working-directory basename. Both need mapping.

    A missing or malformed file is a no-op, never an error.
    """
    if not project:
        return project
    path = config.KNOWLEDGE_DIR / "_aliases.json"
    try:
        aliases = json.loads(path.read_text(encoding="utf-8"))
        return aliases.get(project, project)
    except Exception:
        return project


@dataclass
class IngestReport:
    source: str
    scanned: int = 0          # documents examined
    changed: int = 0          # documents inserted/updated
    skipped: int = 0          # unchanged (up to date)
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def merge(self, other: "IngestReport") -> None:
        self.scanned += other.scanned
        self.changed += other.changed
        self.skipped += other.skipped
        self.errors.extend(other.errors)
        self.notes.extend(other.notes)
