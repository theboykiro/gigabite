"""A note deleted from the knowledge base leaves the index on the next ingest.

Without this, a note re-saved under a new name was found twice — once under the
new file, once under the file that no longer exists — and `doc` kept serving the
deleted one. Written against a temp knowledge base, never the real one.
"""

import tempfile
import unittest
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config, util  # noqa: E402
from gigabite.sources import notes  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402


class TestPruneDeletedNotes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.knowledge = Path(tmp.name) / "knowledge"
        self.folder = self.knowledge / "acme" / "research"
        self.folder.mkdir(parents=True)
        self.store = Store(connect(Path(tmp.name) / "index.db"))

    def _write(self, name, text):
        path = self.folder / name
        path.write_text(f"# {name}\n\n{text}\n", encoding="utf-8")
        return path

    def _doc_id(self, path):
        rel = path.relative_to(self.knowledge).as_posix()
        return util.doc_id(notes.SOURCE, rel)

    def test_deleted_file_is_removed_from_the_index(self):
        old = self._write("2026-07-01-widget-review.md", "Widget churn findings.")
        notes.ingest(self.store, root=self.knowledge)
        old_id = self._doc_id(old)
        self.assertIsNotNone(self.store.get_document(old_id))

        # Re-saved under a new name, then the old file deleted.
        new = self._write("2026-07-01-widget-review-v2.md", "Widget churn findings.")
        old.unlink()
        rep = notes.ingest(self.store, root=self.knowledge)

        self.assertEqual(rep.removed, 1)
        self.assertIsNone(self.store.get_document(old_id))
        self.assertIsNotNone(self.store.get_document(self._doc_id(new)))
        hits = {h["doc_id"] for h in self.store.search("widget churn")}
        self.assertNotIn(old_id, hits)
        self.assertIsNone(self.store.get_signature(
            notes.SOURCE, old.relative_to(self.knowledge).as_posix()))

        # Settled: a second pass has nothing left to remove.
        self.assertEqual(notes.ingest(self.store, root=self.knowledge).removed, 0)

    def test_forced_ingest_prunes_too(self):
        old = self._write("gone.md", "Gadget pricing notes.")
        notes.ingest(self.store, root=self.knowledge)
        old.unlink()
        rep = notes.ingest(self.store, root=self.knowledge, force=True)
        self.assertEqual(rep.removed, 1)
        self.assertIsNone(self.store.get_document(self._doc_id(old)))

    def test_rendering_of_a_document_owned_elsewhere_keeps_the_document(self):
        """Deleting a readable copy must not take the raw export's document with it."""
        doc = Document(
            source=config.SOURCE_CLAUDE_AI, native_id="c1", title="Widget chat",
            ref=str(Path(self.knowledge.parent) / "export.json"),
            messages=[Message(seq=0, role="user", text="Widget anchor pricing.")],
        )
        self.store.upsert_document(doc)
        self.store.commit()
        rendering = self.folder / "widget-chat.md"
        rendering.write_text(
            f"---\ndoc_id: {doc.doc_id}\nsource: claude_ai\n---\n# Widget chat\n\n"
            "Widget anchor pricing.\n", encoding="utf-8")
        notes.ingest(self.store, root=self.knowledge)

        rendering.unlink()
        rep = notes.ingest(self.store, root=self.knowledge)
        self.assertEqual(rep.removed, 0)
        self.assertIsNotNone(self.store.get_document(doc.doc_id))

    def test_case_only_folder_rename_leaves_one_document(self):
        path = self._write("widget-review.md", "Widget churn findings.")
        notes.ingest(self.store, root=self.knowledge)
        old_id = self._doc_id(path)

        renamed = self.knowledge / "acme" / "Research"
        self.folder.rename(self.knowledge / "acme" / "tmp-rename")
        (self.knowledge / "acme" / "tmp-rename").rename(renamed)
        rep = notes.ingest(self.store, root=self.knowledge)

        self.assertEqual(rep.removed, 1)
        self.assertIsNone(self.store.get_document(old_id))
        self.assertIsNotNone(self.store.get_document(
            self._doc_id(renamed / "widget-review.md")))

    def test_unlisted_file_still_on_disk_is_kept(self):
        """Only a file that is actually gone is pruned, not one the walk missed."""
        path = self._write("kept.md", "Sprocket notes.")
        notes.ingest(self.store, root=self.knowledge)
        notes._prune_deleted(self.store, self.knowledge, seen=set())
        self.assertIsNotNone(self.store.get_document(self._doc_id(path)))


if __name__ == "__main__":
    unittest.main()
