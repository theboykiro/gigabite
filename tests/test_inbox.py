"""Tests for the Inbox/ drop folder (features.inbox).

Pure stdlib (unittest). No network, no writes to the real home or the real repo.

    python3 -m unittest discover -s tests        (from the repo root)

Same env-redirect pattern as tests/test_save_routing.py: the stores are pointed at
a temp dir before importing the package, and setUp additionally re-points
config.KNOWLEDGE_DIR / config.INBOX_DROP_DIR at a fresh dir per test — those two
attributes are the surface inbox.py / save.py resolve against at call time.
"""

import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="gigabite-inbox-")
os.environ.setdefault("GIGABITE_CORE_DIR", str(Path(_TMP) / "core"))
os.environ.setdefault("GIGABITE_KNOWLEDGE_DIR", str(Path(_TMP) / "knowledge"))
os.environ.setdefault("GIGABITE_INBOX_DROP_DIR", str(Path(_TMP) / "Inbox"))

from gigabite import config, ingest as ingest_mod  # noqa: E402
from gigabite.features import inbox, save  # noqa: E402
from gigabite.store import Store, connect  # noqa: E402

TODAY = date.today().isoformat()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gigabite-inbox-case-"))
        self.knowledge = self.tmp / "knowledge"
        self.drop = self.tmp / "Inbox"
        self.knowledge.mkdir(parents=True, exist_ok=True)
        self.drop.mkdir(parents=True, exist_ok=True)
        self._orig = (config.KNOWLEDGE_DIR, config.INBOX_DROP_DIR)
        config.KNOWLEDGE_DIR = self.knowledge
        config.INBOX_DROP_DIR = self.drop

    def tearDown(self):
        config.KNOWLEDGE_DIR, config.INBOX_DROP_DIR = self._orig

    # -- helpers ----------------------------------------------------------
    def drop_file(self, relpath: str, content, *, binary: bool = False) -> Path:
        path = self.drop / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def fresh_store(self) -> Store:
        return Store(connect(self.tmp / "index.db"))

    @property
    def filed_dir(self) -> Path:
        return self.drop / inbox.FILED_DIRNAME / TODAY

    @property
    def triage_dir(self) -> Path:
        return self.drop / inbox.TRIAGE_DIRNAME


class TestRouting(_Base):
    def test_root_drop_routes_by_keyword(self):
        save.ensure_project("acme", keywords=["pricing"], layers=["delivery"])
        self.drop_file("meeting.md", "# Notes\n\nPricing anchor agreed with the client.\n")

        rep = inbox.file_inbox()
        self.assertEqual(rep["scanned"], 1)
        self.assertEqual(rep["triaged"], [])
        self.assertEqual(len(rep["filed"]), 1)

        entry = rep["filed"][0]
        self.assertEqual(entry["project"], "acme")
        note = Path(entry["note"])
        self.assertEqual(note.parent, self.knowledge / "acme")   # project root, no layer
        self.assertIn("Pricing anchor agreed", note.read_text(encoding="utf-8"))

    def test_filename_alone_can_route(self):
        save.ensure_project("acme", keywords=["widgets"])
        self.drop_file("widgets-teardown.txt", "body text with no keywords at all\n")
        rep = inbox.file_inbox()
        self.assertEqual(rep["filed"][0]["project"], "acme")

    def test_subfolder_forces_project(self):
        save.ensure_project("acme", keywords=["pricing"])
        save.ensure_project("Beta Co", keywords=["infra"])
        # folder matches the `project:` meta name, case-insensitively
        self.drop_file("Beta Co/thing.md", "Pricing pricing pricing — acme keywords only.\n")

        rep = inbox.file_inbox()
        entry = rep["filed"][0]
        self.assertEqual(entry["project"], "Beta Co")           # forced, not keyword-guessed
        self.assertEqual(entry["layer"], "")
        self.assertEqual(Path(entry["note"]).parent, self.knowledge / "beta-co")

    def test_subfolder_matches_folder_name_case_insensitively(self):
        save.ensure_project("Beta Co")                           # folder is 'beta-co'
        self.drop_file("BETA-CO/thing.md", "no keywords here\n")
        rep = inbox.file_inbox()
        self.assertEqual(rep["filed"][0]["project"], "Beta Co")

    def test_subfolder_project_and_layer(self):
        save.ensure_project("acme", keywords=["pricing"], layers=["delivery"])
        self.drop_file("acme/delivery/kickoff.md", "Kickoff happened.\n")

        rep = inbox.file_inbox()
        entry = rep["filed"][0]
        self.assertEqual((entry["project"], entry["layer"]), ("acme", "delivery"))
        note = Path(entry["note"])
        self.assertEqual(note.parent, self.knowledge / "acme" / "delivery")
        self.assertIn("layer: delivery", note.read_text(encoding="utf-8"))

    def test_unknown_subfolder_falls_back_to_detection(self):
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("random-folder/x.md", "Pricing was discussed.\n")
        rep = inbox.file_inbox()
        self.assertEqual(rep["filed"][0]["project"], "acme")

    def test_frontmatter_title_and_date_are_used(self):
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file(
            "raw.md",
            "---\ntitle: Q3 Pricing Call\ndate: 2026-06-15\n---\n\nPricing agreed.\n",
        )
        note = Path(inbox.file_inbox()["filed"][0]["note"])
        self.assertEqual(note.name, "2026-06-15-q3-pricing-call.md")
        self.assertIn("title: Q3 Pricing Call", note.read_text(encoding="utf-8"))


class TestTriage(_Base):
    def test_ambiguous_file_goes_to_needs_triage(self):
        save.ensure_project("acme", keywords=["pricing"])
        original = self.drop_file("mystery.md", "Nothing here matches any project.\n")

        rep = inbox.file_inbox()
        self.assertEqual(rep["filed"], [])
        self.assertEqual(len(rep["triaged"]), 1)
        self.assertIn("no project", rep["triaged"][0]["reason"])
        # moved, not deleted, and nothing written into the knowledge base
        self.assertFalse(original.exists())
        moved = self.triage_dir / "mystery.md"
        self.assertTrue(moved.exists())
        self.assertEqual(list(self.knowledge.rglob("*.md")), [self.knowledge / "acme" / "_project.md"])

    def test_unsupported_extension_is_not_parsed(self):
        save.ensure_project("acme", keywords=["pricing"])
        # binary payload with a project keyword inside: must NOT be extracted
        self.drop_file("deck.pdf", b"%PDF-1.4 pricing \xff\xfe binary", binary=True)

        rep = inbox.file_inbox()
        self.assertEqual(rep["filed"], [])
        self.assertIn("unsupported file type", rep["triaged"][0]["reason"])
        self.assertTrue((self.triage_dir / "deck.pdf").exists())

    def test_empty_and_non_utf8_files_are_triaged(self):
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("blank.md", "")
        self.drop_file("mojibake.txt", b"\xff\xfe\x00pricing", binary=True)

        rep = inbox.file_inbox()
        reasons = {e["origin"]: e["reason"] for e in rep["triaged"]}
        self.assertEqual(rep["filed"], [])
        self.assertIn("empty", reasons["blank.md"])
        self.assertIn("UTF-8", reasons["mojibake.txt"])

    def test_invalid_json_is_triaged(self):
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("export.json", '{"pricing": ')
        rep = inbox.file_inbox()
        self.assertIn("invalid JSON", rep["triaged"][0]["reason"])

    def test_triage_and_filed_are_not_re_scanned(self):
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("mystery.md", "unroutable\n")
        self.drop_file("call.md", "Pricing agreed.\n")
        inbox.file_inbox()

        second = inbox.file_inbox()          # reserved folders must be skipped
        self.assertEqual(second["scanned"], 0)


class TestOriginals(_Base):
    def test_original_is_moved_to_filed_not_deleted(self):
        save.ensure_project("acme", keywords=["pricing"])
        body = "Pricing anchor agreed with the client.\n"
        original = self.drop_file("acme/call.md", body)

        entry = inbox.file_inbox()["filed"][0]
        self.assertFalse(original.exists())
        moved = Path(entry["filed_to"])
        self.assertEqual(moved.parent, self.filed_dir)
        self.assertEqual(moved.read_text(encoding="utf-8"), body)   # byte-for-byte

    def test_name_collision_is_deduplicated(self):
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("acme/notes.md", "Pricing, first drop.\n")
        inbox.file_inbox()
        self.drop_file("acme/notes.md", "Pricing, second drop.\n")
        rep = inbox.file_inbox()

        names = sorted(p.name for p in self.filed_dir.iterdir())
        self.assertEqual(names, ["notes-2.md", "notes.md"])
        # both notes exist in the knowledge base; neither overwrote the other
        notes = sorted(p.name for p in (self.knowledge / "acme").glob("*.md")
                       if p.name != "_project.md")
        self.assertEqual(len(notes), 2)
        self.assertTrue(Path(rep["filed"][0]["note"]).exists())

    def test_two_drops_same_name_in_one_pass(self):
        save.ensure_project("acme", keywords=["pricing"])
        save.ensure_project("Beta Co", keywords=["infra"])
        self.drop_file("acme/notes.md", "Pricing here.\n")
        self.drop_file("Beta Co/notes.md", "Infra here.\n")

        rep = inbox.file_inbox()
        self.assertEqual(len(rep["filed"]), 2)
        self.assertEqual(sorted(p.name for p in self.filed_dir.iterdir()),
                         ["notes-2.md", "notes.md"])


class TestProvenance(_Base):
    def test_origin_and_share_frontmatter(self):
        save.ensure_project("acme", keywords=["pricing"], layers=["delivery"])
        self.drop_file("acme/delivery/call.md", "Pricing agreed.\n")

        note = Path(inbox.file_inbox()["filed"][0]["note"])
        text = note.read_text(encoding="utf-8")
        self.assertIn("origin: inbox/acme/delivery/call.md", text)
        self.assertIn("share: private", text)
        # standard fields still present and in front of the extras
        self.assertLess(text.index("project: acme"), text.index("origin:"))

    def test_extra_frontmatter_is_opt_in(self):
        # existing callers get byte-identical output (no origin/share fields)
        path = save.save_note("plain", project="acme", ts="2026-06-15")
        self.assertNotIn("share:", path.read_text(encoding="utf-8"))

    def test_extras_cannot_override_reserved_fields(self):
        path = save.save_note("x", project="acme", ts="2026-06-15",
                              meta={"project": "evil", "share": "private"})
        text = path.read_text(encoding="utf-8")
        self.assertIn("project: acme", text)
        self.assertNotIn("evil", text)

    def test_multiline_extra_value_cannot_break_frontmatter(self):
        path = save.save_note("x", project="acme", ts="2026-06-15",
                              meta={"origin": "a\n---\nb"})
        text = path.read_text(encoding="utf-8")
        self.assertIn("origin: a --- b", text)
        self.assertEqual(text.count("---"), 3)      # open, the escaped value, close


class TestDryRun(_Base):
    def test_dry_run_writes_and_moves_nothing(self):
        save.ensure_project("acme", keywords=["pricing"])
        kept = self.drop_file("call.md", "Pricing agreed.\n")
        unroutable = self.drop_file("mystery.md", "nothing matches\n")

        rep = inbox.file_inbox(dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertEqual(rep["filed"][0]["project"], "acme")
        self.assertEqual(rep["filed"][0]["note"], "")
        self.assertEqual(len(rep["triaged"]), 1)
        self.assertEqual(rep["triaged"][0]["moved_to"], "")

        # everything still exactly where it was
        self.assertTrue(kept.exists())
        self.assertTrue(unroutable.exists())
        self.assertFalse(self.filed_dir.exists())
        self.assertFalse(self.triage_dir.exists())
        self.assertEqual([p.name for p in self.knowledge.rglob("*.md")], ["_project.md"])


class TestExtraction(_Base):
    def test_readme_is_never_filed(self):
        self.drop_file("README.md", "# Inbox\n\ninstructions, not content\n")
        self.assertEqual(inbox.file_inbox()["scanned"], 0)

    def test_vtt_cues_are_stripped(self):
        vtt = ("WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n"
               "Alice: the pricing anchor holds.\n\n"
               "2\n00:00:04.000 --> 00:00:06.500\nBob: agreed.\n")
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("acme/transcript.vtt", vtt)

        note = Path(inbox.file_inbox()["filed"][0]["note"])
        body = note.read_text(encoding="utf-8")
        self.assertIn("Alice: the pricing anchor holds.", body)
        self.assertIn("Bob: agreed.", body)
        self.assertNotIn("-->", body)
        self.assertNotIn("WEBVTT", body)

    def test_json_is_rendered_as_readable_lines(self):
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("acme/export.json",
                       '{"title": "Kickoff", "attendees": ["Ann", "Bo"], '
                       '"notes": {"summary": "pricing anchor agreed"}}')
        body = Path(inbox.file_inbox()["filed"][0]["note"]).read_text(encoding="utf-8")
        self.assertIn("title: Kickoff", body)
        self.assertIn("attendees.1: Ann", body)
        self.assertIn("notes.summary: pricing anchor agreed", body)


class TestIngestWiring(_Base):
    def _settle(self, rel: str) -> Path:
        """Back-date a drop past the settle window so an ingest pass will take it.

        ingest applies inbox.SETTLE_SECONDS, so a file created microseconds ago is
        deliberately left alone; tests must age it to exercise the filing path.
        """
        path = config.INBOX_DROP_DIR / rel
        old = time.time() - (inbox.SETTLE_SECONDS + 60)
        os.utime(path, (old, old))
        return path

    def test_dropped_file_is_filed_and_indexed_in_one_pass(self):
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("acme/call.md", "Pricing anchor agreed with the client.\n")
        self._settle("acme/call.md")

        st = self.fresh_store()
        reports = ingest_mod.run(st, sources=[config.SOURCE_NOTE])
        rep = reports[config.SOURCE_NOTE]
        self.assertEqual(rep.changed, 1)
        self.assertTrue(any("inbox: acme/call.md" in n for n in rep.notes))
        self.assertEqual(rep.errors, [])

        hits = st.search("anchor", project="acme")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"], config.SOURCE_NOTE)

    def test_ingest_leaves_a_still_being_written_file_for_the_next_pass(self):
        """A file mid-save must not be filed and moved out from under the writer."""
        save.ensure_project("acme", keywords=["pricing"])
        dropped = self.drop_file("acme/half-written.md", "Pricing anchor ag")

        st = self.fresh_store()
        rep = ingest_mod.run(st, sources=[config.SOURCE_NOTE])[config.SOURCE_NOTE]
        self.assertEqual(rep.errors, [])
        self.assertTrue(any("still being written" in n for n in rep.notes))
        self.assertTrue(dropped.exists(), "the drop must be left exactly where it was")

        # once it settles, the very next pass files it
        self._settle("acme/half-written.md")
        rep2 = ingest_mod.run(self.fresh_store(), sources=[config.SOURCE_NOTE])[config.SOURCE_NOTE]
        self.assertTrue(any("inbox: acme/half-written.md → acme" in n for n in rep2.notes))
        self.assertFalse(dropped.exists(), "filing moves the original out of the drop root")

    def test_manual_file_command_does_not_wait_for_settle(self):
        """`gigabite file` is explicit: a file just dropped by hand files now."""
        save.ensure_project("acme", keywords=["pricing"])
        self.drop_file("acme/just-dropped.md", "Pricing anchor agreed.\n")
        rep = inbox.file_inbox()
        self.assertEqual(rep["waiting"], [])
        self.assertEqual(len(rep["filed"]), 1)

    def test_filing_failure_does_not_abort_ingest(self):
        config.INBOX_DROP_DIR = self.tmp / "nope-does-not-exist"
        st = self.fresh_store()
        reports = ingest_mod.run(st, sources=[config.SOURCE_NOTE])
        self.assertEqual(reports[config.SOURCE_NOTE].errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
