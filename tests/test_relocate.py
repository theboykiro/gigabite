"""Bringing an older ~/Knowledge layout up to date.

This code moves the only copy of real content, so the tests lean on the
properties that matter: nothing that holds content is deleted, nothing is
overwritten, running it twice is harmless, and an ambiguous situation stops
rather than guesses.
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
        (self.legacy / "_proposals").mkdir()
        (self.legacy / "_proposals" / "2026-07-20.md").write_text("# proposal")
        (self.legacy / "_aliases.json").write_text('{"gigabyte": "gigabite"}')
        self.repo_inbox.mkdir(parents=True)
        (self.repo_inbox / "README.md").write_text("committed guide")
        (self.repo_inbox / "dropped.md").write_text("# dropped")

    def _plan(self):
        return relocate.plan(legacy=self.legacy, target=self.target,
                             repo_inbox=self.repo_inbox)

    def _machine(self, *parts):
        return self.target.joinpath(config.MACHINE_DIRNAME, *parts)

    def test_plan_changes_nothing_on_disk(self):
        before = sorted(p.relative_to(self.root).as_posix()
                        for p in self.root.rglob("*"))
        self._plan()
        after = sorted(p.relative_to(self.root).as_posix()
                       for p in self.root.rglob("*"))
        self.assertEqual(before, after)

    def test_projects_stay_where_they_are(self):
        relocate.apply(self._plan())
        self.assertFalse(self.legacy.exists())
        self.assertTrue((self.target / "acme" / "meetings" / "sync.md").exists())

    def test_all_machinery_moves_into_one_hidden_folder(self):
        relocate.apply(self._plan())
        self.assertTrue(self._machine("imports", "granola", "m.md").exists())
        self.assertTrue(self._machine("archive", "old.md").exists())
        self.assertTrue(self._machine("proposals", "2026-07-20.md").exists())
        self.assertTrue(self._machine("aliases.json").exists())

    def test_nothing_but_projects_and_readme_is_visible_afterwards(self):
        relocate.apply(self._plan())
        visible = sorted(q.name for q in self.target.iterdir()
                         if not q.name.startswith("."))
        # 'acme' is the project; 'dropped.md' was never filed and so has no
        # project — it sits at the root, visible, for the user to place.
        self.assertEqual(visible, ["acme", "dropped.md"])

    def test_no_content_is_lost(self):
        before = {p.name for p in self.legacy.rglob("*") if p.is_file()}
        relocate.apply(self._plan())
        after = {p.name for p in self.target.rglob("*") if p.is_file()}
        # aliases.json is renamed on the way in; everything else keeps its name.
        before.discard("_aliases.json")
        self.assertTrue(before.issubset(after), f"lost: {before - after}")
        self.assertTrue(self._machine("aliases.json").exists())
    def test_already_filed_originals_are_kept_but_not_indexable(self):
        filed = self.target / "Inbox" / "_filed" / "2026-08-05"
        filed.mkdir(parents=True)
        (filed / "note.md").write_text("# already filed elsewhere")
        relocate.apply(self._plan())
        kept = self._machine("originals", "inbox", "_filed", "2026-08-05", "note.md")
        self.assertTrue(kept.exists(), "the original must be preserved")
        self.assertFalse((self.target / "note.md").exists(),
                         "a note already filed must not come back as content")

    def test_committed_repo_readme_is_never_deleted(self):
        relocate.apply(self._plan())
        self.assertTrue((self.repo_inbox / "README.md").exists())

    def test_obsolete_drop_folder_readme_is_removed(self):
        drop = self.target / "Inbox"
        drop.mkdir(parents=True)
        (drop / "README.md").write_text("drop files here — no longer true")
        relocate.apply(self._plan())
        self.assertFalse((drop / "README.md").exists())
        self.assertFalse(drop.exists(), "the emptied drop folder should be gone")

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

    def test_existing_file_is_not_overwritten(self):
        (self.target / "dropped.md").parent.mkdir(parents=True, exist_ok=True)
        (self.target / "dropped.md").write_text("newer version")
        relocate.apply(self._plan())
        self.assertEqual((self.target / "dropped.md").read_text(), "newer version")
        self.assertTrue((self.repo_inbox / "dropped.md").exists(),
                        "the un-moved original must remain")

    def test_nothing_to_do_when_there_is_no_legacy_root(self):
        import shutil
        shutil.rmtree(self.legacy)
        p = self._plan()
        self.assertFalse(any(s.what == "knowledge base" and s.actionable
                             for s in p.steps))

    def test_the_knowledge_root_is_never_pruned(self):
        import shutil
        shutil.rmtree(self.legacy)
        shutil.rmtree(self.repo_inbox)
        self.target.mkdir(parents=True, exist_ok=True)
        relocate.apply(self._plan())
        self.assertTrue(self.target.exists())


class TestLegacyFoldersAreNotIndexed(unittest.TestCase):
    """An un-migrated store must not be indexed out of its old machinery folders."""

    def test_notes_ingester_skips_legacy_system_folders(self):
        from gigabite.sources import notes
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "Knowledge"
        (root / "acme").mkdir(parents=True)
        (root / "acme" / "real.md").write_text("# a real note")
        for legacy in ("Inbox", "_sources", "_archive", "_proposals"):
            (root / legacy).mkdir()
            (root / legacy / "stray.md").write_text(f"# in {legacy}")
        (root / config.MACHINE_DIRNAME / "imports").mkdir(parents=True)
        (root / config.MACHINE_DIRNAME / "imports" / "raw.md").write_text("# raw")

        found = {p.name for p in notes._iter_content_files(root)}
        self.assertIn("real.md", found)
        self.assertNotIn("stray.md", found,
                         "content in a legacy machinery folder must not be indexed")
        self.assertNotIn("raw.md", found,
                         "nothing inside .gigabite/ is content")


if __name__ == "__main__":
    unittest.main()
