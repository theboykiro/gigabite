"""Claude Code auto-memory notes: indexed, and filed by their words, not their folder.

A memory directory is named after the working directory its session ran in. The
fact inside it can be about any project, so the project comes from keyword
routing over the note, and a note nothing routes stays unfiled.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config, ingest  # noqa: E402
from gigabite.sources import claude_memory  # noqa: E402
from gigabite.store import Store, connect  # noqa: E402

WIDGET_NOTE = """---
name: acme-fiscal-calendar
description: "Acme quarters are fiscal - the year starts in April"
metadata:
  node_type: memory
  type: project
  modified: 2026-10-23T12:25:09.021Z
---

The financial year starts 1 April, so Q1 = Apr-Jun.

**How to apply:** label every widget roadmap quarter with its months.
"""

PLAIN_NOTE = """---
name: tone-direct
description: lead with the answer
metadata:
  type: feedback
---

Open with the answer, no preamble.
"""


class _Memory(_harness.TempRoot):
    def setUp(self):
        super().setUp()
        d = config.KNOWLEDGE_DIR / "acme"
        d.mkdir(parents=True, exist_ok=True)
        (d / "_project.md").write_text(
            "---\nproject: acme\nkeywords: acme, widget\nlayers: \n---\n",
            encoding="utf-8")
        # The folder is named after a *different* project's repo, on purpose.
        self.projects = config.CLAUDE_CODE_PROJECTS_DIR
        self.mem = self.projects / "-Users-someone-toolrepo" / "memory"
        self.mem.mkdir(parents=True, exist_ok=True)
        self.st = Store(connect(self.db))
        self.addCleanup(self.st.conn.close)

    def write(self, name, text):
        (self.mem / name).write_text(text, encoding="utf-8")


class TestMemoryNotes(_Memory):
    def test_frontmatter_parsed_into_document(self):
        self.write("acme-fiscal-calendar.md", WIDGET_NOTE)
        doc = claude_memory.document_from_memory(
            self.mem / "acme-fiscal-calendar.md", self.projects)
        self.assertEqual(doc.source, config.SOURCE_NOTE)
        self.assertEqual(doc.title, "acme fiscal calendar")
        self.assertEqual(doc.extra["memory_name"], "acme-fiscal-calendar")
        self.assertEqual(doc.extra["memory_type"], "project")
        self.assertTrue(doc.created_utc.startswith("2026-10-23"))
        self.assertNotIn("node_type", doc.messages[0].text)
        self.assertIn("starts 1 April", doc.messages[0].text)
        self.assertTrue(doc.messages[0].text.startswith("Acme quarters are fiscal"))

    def test_found_by_its_name(self):
        self.write("acme-fiscal-calendar.md", WIDGET_NOTE)
        claude_memory.ingest(self.st)
        hits = self.st.search("acme fiscal calendar")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "acme fiscal calendar")

    def test_routed_by_keywords_not_folder(self):
        self.write("acme-fiscal-calendar.md", WIDGET_NOTE)
        rep = claude_memory.ingest(self.st)
        self.assertEqual(rep.changed, 1)
        hits = self.st.search("fiscal quarters")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["project"], "acme")
        self.assertEqual(hits[0]["source"], "note")

    def test_no_project_resolves_to_unfiled(self):
        # The folder says "toolrepo"; nothing in the note does, so no project.
        self.write("tone-direct.md", PLAIN_NOTE)
        claude_memory.ingest(self.st)
        hits = self.st.search("preamble")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["project"], "")

    def test_memory_index_is_skipped(self):
        self.write("MEMORY.md", "# Memory index\n\n- [Acme](acme.md) - acme widget\n")
        rep = claude_memory.ingest(self.st)
        self.assertEqual(rep.scanned, 0)
        self.assertFalse(self.st.search("index"))

    def test_reingest_is_incremental(self):
        self.write("acme-fiscal-calendar.md", WIDGET_NOTE)
        self.write("tone-direct.md", PLAIN_NOTE)
        self.assertEqual(claude_memory.ingest(self.st).changed, 2)
        rep = claude_memory.ingest(self.st)
        self.assertEqual(rep.changed, 0)
        self.assertEqual(rep.skipped, rep.scanned)

    def test_full_ingest_includes_memory_in_the_note_pass(self):
        self.write("acme-fiscal-calendar.md", WIDGET_NOTE)
        reports = ingest.run(self.st, sources=[config.SOURCE_NOTE])
        self.assertEqual(reports[config.SOURCE_NOTE].changed, 1)
        self.assertTrue(self.st.search("fiscal", project="acme"))


if __name__ == "__main__":
    unittest.main()
