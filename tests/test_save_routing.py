"""Tests for knowledge-write routing (ARCHITECTURE §4) and the notes ingester.

Pure stdlib (unittest). No network, no writes to the real home.

    python3 -m unittest discover -s tests        (from the repo root)

Note: config.KNOWLEDGE_DIR is bound to the env var at *first* import of the
config module, which — under `discover` — may be another test's temp dir. Each
test here re-points config.KNOWLEDGE_DIR at its own fresh dir in setUp, which is
exactly the surface save.py / notes.py resolve against at call time.
"""

import tempfile
import unittest
from pathlib import Path

# Redirect stores into a temp dir BEFORE importing the package (env-redirect
# pattern from tests/test_gigabite.py). setUp additionally isolates each test.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config  # noqa: E402
from gigabite.store import Store, connect  # noqa: E402
from gigabite.features import save  # noqa: E402
from gigabite.sources import notes  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gigabite-routing-case-"))
        self.knowledge = self.tmp / "knowledge"
        self.knowledge.mkdir(parents=True, exist_ok=True)
        # point every knowledge-resolving call at this test's dir
        self._orig_knowledge = config.KNOWLEDGE_DIR
        config.KNOWLEDGE_DIR = self.knowledge

    def tearDown(self):
        config.KNOWLEDGE_DIR = self._orig_knowledge

    def fresh_store(self) -> Store:
        return Store(connect(self.tmp / "index.db"))


class TestSlugify(_Base):
    def test_slug_and_traversal(self):
        self.assertEqual(save.slugify("Acme Corp Kickoff"), "acme-corp-kickoff")
        self.assertEqual(save.slugify("Q3 Pricing!!"), "q3-pricing")
        # separators and dot runs collapse to nothing dangerous
        self.assertEqual(save.slugify("../../etc/passwd"), "etcpasswd")
        self.assertEqual(save.slugify(".."), "")


class TestSaveRouting(_Base):
    def test_note_writes_under_knowledge_not_cwd(self):
        # run from an unrelated working directory (a "code" repo)
        workdir = self.tmp / "some-code-repo"
        workdir.mkdir()
        cwd = os.getcwd()
        os.chdir(workdir)
        try:
            path = save.save_note(
                "Pricing anchor decided for the enterprise tier.",
                project="acme",
                layer="delivery",
                title="Pricing call",
                ts="2026-06-15T10:00:00Z",
            )
        finally:
            os.chdir(cwd)

        # routed under ~/.knowledge/{project}/{layer}/, never the working dir
        self.assertTrue(str(path).startswith(str(self.knowledge)))
        self.assertEqual(path.parent, self.knowledge / "acme" / "delivery")
        self.assertEqual(path.name, "2026-06-15-pricing-call.md")
        self.assertTrue(path.exists())
        # nothing leaked into the working directory
        self.assertEqual(list(workdir.rglob("*.md")), [])

        content = path.read_text(encoding="utf-8")
        self.assertIn("title: Pricing call", content)
        self.assertIn("layer: delivery", content)

    def test_note_without_layer_lands_at_project_root(self):
        path = save.save_note("A quick thought.", project="acme", ts="2026-06-16")
        self.assertEqual(path.parent, self.knowledge / "acme")

    def test_path_traversal_is_neutralised(self):
        path = save.save_note(
            "escape attempt",
            project="../../evil",
            layer="../secrets",
            title="x",
            ts="2026-06-17",
        )
        resolved = path.resolve()
        # stays inside the knowledge base; no parent escape
        self.assertTrue(str(resolved).startswith(str(self.knowledge.resolve())))
        self.assertEqual(path.parent, self.knowledge / "evil" / "secrets")

    def test_collision_gets_suffixed(self):
        a = save.save_note("one", project="acme", title="Same", ts="2026-06-18")
        b = save.save_note("two", project="acme", title="Same", ts="2026-06-18")
        self.assertNotEqual(a, b)
        self.assertEqual(b.name, "2026-06-18-same-2.md")


class TestEnsureProject(_Base):
    def test_creates_project_meta(self):
        proj_dir = save.ensure_project(
            "Acme", keywords=["pricing", "roadmap"], layers=["delivery", "strategy"]
        )
        meta = proj_dir / "_project.md"
        self.assertEqual(proj_dir, self.knowledge / "acme")
        self.assertTrue(meta.exists())
        text = meta.read_text(encoding="utf-8")
        self.assertIn("project: Acme", text)
        self.assertIn("keywords: pricing, roadmap", text)
        self.assertIn("layers: delivery, strategy", text)

    def test_existing_meta_is_not_clobbered(self):
        proj_dir = save.ensure_project("Acme", keywords=["a"])
        meta = proj_dir / "_project.md"
        meta.write_text("custom edited meta", encoding="utf-8")
        save.ensure_project("Acme", keywords=["b"])  # second call
        self.assertEqual(meta.read_text(encoding="utf-8"), "custom edited meta")

    def test_list_projects(self):
        save.ensure_project("Acme", keywords=["pricing"], layers=["delivery"])
        save.ensure_project("Beta Co", keywords=["infra", "cost"])
        listed = {p["name"]: p for p in save.list_projects()}
        self.assertEqual(set(listed), {"Acme", "Beta Co"})
        self.assertEqual(listed["Acme"]["keywords"], ["pricing"])
        self.assertEqual(listed["Acme"]["layers"], ["delivery"])
        self.assertEqual(listed["Beta Co"]["keywords"], ["infra", "cost"])


class TestNotesIngester(_Base):
    def test_indexes_notes_and_skips_reserved(self):
        # a real saved note
        save.ensure_project("acme", keywords=["pricing"])
        save.save_note(
            "Pricing anchor decided at the kickoff.",
            project="acme", layer="delivery", title="Kickoff", ts="2026-06-15",
        )
        # the machinery folder and the legacy names for it must be ignored
        (self.knowledge / config.MACHINE_DIRNAME / "imports").mkdir(
            parents=True, exist_ok=True)
        (self.knowledge / config.MACHINE_DIRNAME / "imports" / "raw.md").write_text(
            "raw import about widgets", encoding="utf-8")
        (self.knowledge / "_sources").mkdir(exist_ok=True)
        (self.knowledge / "_sources" / "dropme.md").write_text(
            "legacy scratch about widgets", encoding="utf-8")
        (self.knowledge / "_archive").mkdir(exist_ok=True)
        (self.knowledge / "_archive" / "old.md").write_text(
            "decayed history mentioning widgets", encoding="utf-8")
        # README and _project.md are meta, not knowledge
        (self.knowledge / "acme" / "README.md").write_text(
            "readme boilerplate about widgets", encoding="utf-8")

        st = self.fresh_store()
        rep = notes.ingest(st)
        self.assertEqual(rep.changed, 1)                 # only the Kickoff note

        hits = st.search("anchor", project="acme")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"], "note")
        self.assertEqual(hits[0]["title"], "Kickoff")
        # layer captured in extra
        doc = st.get_document(hits[0]["doc_id"])

        # reserved content is NOT searchable
        self.assertFalse(st.search("widgets"))

    def test_layer_recorded_and_native_id_is_relative(self):
        save.save_note("layered note body", project="acme", layer="strategy",
                        title="Plan", ts="2026-06-20")
        st = self.fresh_store()
        notes.ingest(st)
        hits = st.search("layered")
        self.assertTrue(hits)
        doc = st.get_document(hits[0]["doc_id"])
        # native_id is the path relative to the knowledge root
        self.assertEqual(
            doc["native_id"], "acme/strategy/2026-06-20-plan.md")

    def test_reingest_is_incremental(self):
        save.save_note("first note", project="acme", title="One", ts="2026-06-21")
        st = self.fresh_store()
        rep1 = notes.ingest(st)
        self.assertEqual(rep1.changed, 1)
        rep2 = notes.ingest(st)                           # unchanged files
        self.assertEqual(rep2.changed, 0)
        self.assertEqual(rep2.skipped, rep2.scanned)


if __name__ == "__main__":
    unittest.main(verbosity=2)
