"""gigabite tidy: proposes only unambiguous moves, never overwrites, undo restores."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402

from gigabite import config  # noqa: E402
from gigabite.features import tidy  # noqa: E402

OLD = 1_000_000.0   # mtime far in the past; `now` below is well after it


class TidyTest(_harness.TempRoot):
    def setUp(self):
        super().setUp()
        self.p = config.KNOWLEDGE_DIR / "acme"
        for d in ("Slice 1", "Slice 2", "meetings", "Analytics"):
            (self.p / d).mkdir(parents=True)
        (self.p / "_project.md").write_text("---\nproject: acme\nkeywords: acme\nlayers: \n---\n")
        self.now = OLD + 10_000

    def w(self, rel, text="x"):
        f = self.p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        os.utime(f, (OLD, OLD))
        return f

    def plan(self):
        return tidy.plan_project(self.p, "acme", self.now)

    def dst(self, plan, name):
        return {os.path.basename(m.src): m for m in plan.moves}.get(name)

    def test_folder_name_match(self):
        self.w("slice-2-kpis.png", "a")
        m = self.dst(self.plan(), "slice-2-kpis.png")
        self.assertEqual(os.path.basename(os.path.dirname(m.dst)), "Slice 2")

    def test_slice_10_is_not_slice_1(self):
        self.w("slice-10-kpis.png", "a")
        self.assertIsNone(self.dst(self.plan(), "slice-10-kpis.png"))

    def test_two_folders_named_is_left_alone(self):
        self.w("slice-1-analytics-params.xlsx", "a")
        plan = self.plan()
        self.assertIsNone(self.dst(plan, "slice-1-analytics-params.xlsx"))
        self.assertIn("slice-1-analytics-params.xlsx", plan.unplaced["acme"])

    def test_meeting_note_goes_to_meetings(self):
        self.w("2026-11-06-sync.md", "---\ntitle: t\nsource: meeting\n---\nbody")
        m = self.dst(self.plan(), "2026-11-06-sync.md")
        self.assertEqual(os.path.basename(os.path.dirname(m.dst)), "meetings")

    def test_layer_without_existing_folder_is_not_invented(self):
        self.w("n.md", "---\nlayer: strategy\n---\nbody")
        self.assertIsNone(self.dst(self.plan(), "n.md"))

    def test_duplicate_keeps_human_name(self):
        self.w("Brand Guide.pdf", "same")
        self.w("brand-guide.pdf", "same")
        plan = self.plan()
        m = self.dst(plan, "brand-guide.pdf")
        self.assertEqual(m.kind, "duplicate")
        self.assertIsNone(self.dst(plan, "Brand Guide.pdf"))

    def test_copy_already_filed_makes_top_level_a_duplicate(self):
        self.w("a.pdf", "same")
        self.w("meetings/a.pdf", "same")
        self.assertEqual(self.dst(self.plan(), "a.pdf").kind, "duplicate")

    def test_recent_and_locked_files_are_skipped(self):
        f = self.w("slice-1-new.png", "a")
        os.utime(f, (self.now - 5, self.now - 5))
        self.w("slice-1-doc.docx", "b")
        self.w("~$slice-1-doc.docx", "lock")
        plan = self.plan()
        self.assertIsNone(self.dst(plan, "slice-1-new.png"))
        self.assertIsNone(self.dst(plan, "slice-1-doc.docx"))
        self.assertEqual(len(plan.skipped), 2)

    def test_reserved_files_never_move(self):
        self.w("people.md", "---\nsource: meeting\n---\n")
        self.assertIsNone(self.dst(self.plan(), "people.md"))

    def test_apply_never_overwrites_and_undo_restores(self):
        self.w("slice-1-a.png", "new")
        self.w("Slice 1/slice-1-a.png", "existing")
        tidy.apply(self.plan())
        self.assertEqual((self.p / "Slice 1" / "slice-1-a.png").read_text(), "existing")
        self.assertEqual((self.p / "Slice 1" / "slice-1-a-2.png").read_text(), "new")
        self.assertFalse((self.p / "slice-1-a.png").exists())
        _, restored, _ = tidy.undo_last()
        self.assertEqual(restored, 1)
        self.assertEqual((self.p / "slice-1-a.png").read_text(), "new")


if __name__ == "__main__":
    unittest.main()
