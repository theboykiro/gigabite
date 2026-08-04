"""Relocating a legacy ~/.knowledge to the visible ~/Knowledge layout.

This code moves the only copy of real content, so the tests lean on the
properties that matter: nothing is deleted, nothing is overwritten, running it
twice is harmless, and an ambiguous situation stops rather than guesses.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gigabite import config  # noqa: E402
from gigabite.features import relocate  # noqa: E402


class TestRelocate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.legacy = self.root / ".knowledge"
        self.target = self.root / "Knowledge"
        self.repo_inbox = self.root / "repo" / "Inbox"

        (self.legacy / "acme" / "meetings").mkdir(parents=True)
        (self.legacy / "acme" / "meetings" / "sync.md").write_text("# sync")
        (self.legacy / "_inbox" / "granola").mkdir(parents=True)
        (self.legacy / "_inbox" / "granola" / "m.md").write_text("# meeting")
        (self.legacy / "_historical").mkdir()
        (self.legacy / "_historical" / "old.md").write_text("# old")
        self.repo_inbox.mkdir(parents=True)
        (self.repo_inbox / "README.md").write_text("committed guide")
        (self.repo_inbox / "dropped.md").write_text("# dropped")

    def _plan(self):
        return relocate.plan(legacy=self.legacy, target=self.target,
                             repo_inbox=self.repo_inbox)

    def test_plan_changes_nothing_on_disk(self):
        before = sorted(p.relative_to(self.root).as_posix()
                        for p in self.root.rglob("*"))
        self._plan()
        after = sorted(p.relative_to(self.root).as_posix()
                       for p in self.root.rglob("*"))
        self.assertEqual(before, after)

    def test_moves_everything_to_the_visible_layout(self):
        relocate.apply(self._plan())
        self.assertFalse(self.legacy.exists())
        self.assertTrue((self.target / "acme" / "meetings" / "sync.md").exists())
        self.assertTrue((self.target / "_sources" / "granola" / "m.md").exists())
        self.assertTrue((self.target / "_archive" / "old.md").exists())
        self.assertTrue((self.target / "Inbox" / "dropped.md").exists())

    def test_no_content_is_lost(self):
        before = {p.name for p in self.legacy.rglob("*") if p.is_file()}
        relocate.apply(self._plan())
        after = {p.name for p in self.target.rglob("*") if p.is_file()}
        self.assertTrue(before.issubset(after), f"lost: {before - after}")

    def test_committed_readme_stays_in_the_repo(self):
        relocate.apply(self._plan())
        self.assertTrue((self.repo_inbox / "README.md").exists())
        self.assertFalse((self.target / "Inbox" / "README.md").exists())

    def test_running_twice_is_harmless(self):
        first = relocate.apply(self._plan())
        self.assertTrue(first)
        p2 = self._plan()
        self.assertEqual(p2.actionable, [], "second run should have nothing to do")
        self.assertEqual(relocate.apply(p2), [])
        self.assertTrue((self.target / "acme" / "meetings" / "sync.md").exists())

    def test_refuses_to_merge_two_populated_roots(self):
        self.target.mkdir()
        (self.target / "existing.md").write_text("mine")
        p = self._plan()
        self.assertTrue(p.warnings, "expected a refusal warning")
        self.assertFalse(any(s.what == "knowledge base" and s.actionable
                             for s in p.steps))
        # and the legacy content is untouched
        self.assertTrue((self.legacy / "acme" / "meetings" / "sync.md").exists())

    def test_existing_drop_file_is_not_overwritten(self):
        (self.target / "Inbox").mkdir(parents=True)
        (self.target / "Inbox" / "dropped.md").write_text("newer version")
        relocate.apply(self._plan())
        self.assertEqual((self.target / "Inbox" / "dropped.md").read_text(),
                         "newer version")
        self.assertTrue((self.repo_inbox / "dropped.md").exists(),
                        "the un-moved original must remain")

    def test_nothing_to_do_when_there_is_no_legacy_root(self):
        import shutil
        shutil.rmtree(self.legacy)
        p = self._plan()
        self.assertFalse(any(s.what == "knowledge base" and s.actionable
                             for s in p.steps))


class TestDropFolderIsNotAProject(unittest.TestCase):
    """The drop box is called 'Inbox', so it has no underscore to disqualify it."""

    def test_notes_ingester_skips_the_drop_folder(self):
        from gigabite.sources import notes
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "Knowledge"
        (root / "acme").mkdir(parents=True)
        (root / "acme" / "real.md").write_text("# a real note")
        (root / "Inbox").mkdir()
        (root / "Inbox" / "waiting.md").write_text("# not filed yet")

        old_root, old_drop = config.KNOWLEDGE_DIR, config.INBOX_DROP_DIR
        config.KNOWLEDGE_DIR, config.INBOX_DROP_DIR = root, root / "Inbox"
        try:
            found = {p.name for p in notes._iter_note_files(root)}
        finally:
            config.KNOWLEDGE_DIR, config.INBOX_DROP_DIR = old_root, old_drop

        self.assertIn("real.md", found)
        self.assertNotIn("waiting.md", found,
                         "a file awaiting filing must not be indexed from the drop box")


if __name__ == "__main__":
    unittest.main()
