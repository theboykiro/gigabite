"""Source ingesters. Each module exposes:

    ingest(store, **opts) -> IngestReport

and yields gigabite.store.Document objects the store upserts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
