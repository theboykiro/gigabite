"""End-to-end tests for gigabite. Pure stdlib (unittest). No network, no home writes.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

# Redirect all stores into a temp dir BEFORE importing the package.
_TMP = tempfile.mkdtemp(prefix="gigabite-test-")
os.environ["GIGABITE_CORE_DIR"] = str(Path(_TMP) / "core")
os.environ["GIGABITE_KNOWLEDGE_DIR"] = str(Path(_TMP) / "knowledge")

from gigabite import config, util  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402
from gigabite.sources import claude_ai, claude_code, granola, granola_live  # noqa: E402


def fresh_store(name) -> Store:
    db = Path(_TMP) / f"{name}.db"
    if db.exists():
        db.unlink()
    return Store(connect(db))


class TestUtil(unittest.TestCase):
    def test_fts_query_is_safe(self):
        # quotes/operators that would break a raw MATCH must be neutralised
        self.assertEqual(util.to_fts_query('pricing "model"'), '"pricing" "model"')
        self.assertEqual(util.to_fts_query("acme*"), '"acme"*')
        self.assertEqual(util.to_fts_query('NOT ) OR ('), '"NOT" "OR"')
        self.assertEqual(util.to_fts_query("   "), "")

    def test_coalesce_blocks(self):
        content = [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "name": "x"},
            {"type": "tool_result", "content": [{"type": "text", "text": "world"}]},
        ]
        self.assertEqual(util.coalesce_blocks(content), "hmm\nhello\nworld")
        self.assertEqual(util.coalesce_blocks("plain"), "plain")

    def test_time_normalisation(self):
        self.assertTrue(util.to_iso_utc("2026-06-01T10:00:00Z").startswith("2026-06-01T10:00:00"))
        self.assertEqual(util.to_iso_utc(""), "")
        self.assertTrue(util.to_iso_utc(1_700_000_000).startswith("2023-"))


class TestStore(unittest.TestCase):
    def test_upsert_search_and_incremental(self):
        st = fresh_store("store")
        doc = Document(
            source="claude_code", native_id="s1", title="Pricing chat", project="acme",
            created_utc="2026-06-01T10:00:00+00:00", updated_utc="2026-06-01T10:05:00+00:00",
            messages=[
                Message(0, "user", "How do we price the enterprise tier?"),
                Message(1, "assistant", "Anchor pricing to seats and usage."),
            ],
        )
        self.assertTrue(st.upsert_document(doc))       # first insert changes index
        self.assertFalse(st.upsert_document(doc))      # identical -> no change

        hits = st.search("pricing enterprise")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "Pricing chat")
        self.assertIn("«", hits[0]["snippet"])         # snippet highlighting present

        # title matches are boosted above body-only matches
        st.upsert_document(Document(
            source="claude_ai", native_id="s2", title="Random",
            messages=[Message(0, "user", "pricing " * 3)]))
        st.upsert_document(Document(
            source="claude_ai", native_id="s3", title="Pricing",
            messages=[Message(0, "user", "unrelated body text")]))
        ranked = st.search("pricing")
        self.assertEqual(ranked[0]["title"], "Pricing")

        # filters
        self.assertTrue(all(h["source"] == "claude_code"
                            for h in st.search("pricing", sources=["claude_code"])))
        self.assertTrue(all(h["project"] == "acme"
                            for h in st.search("pricing", project="acme")))

        # doc reconstruction preserves message order
        full = st.get_document(doc.doc_id)
        self.assertEqual([m["seq"] for m in full["messages"]], [0, 1])

        # deletion
        st.delete_document(doc.doc_id)
        self.assertIsNone(st.get_document(doc.doc_id))

    def test_malformed_query_never_raises(self):
        st = fresh_store("store2")
        st.upsert_document(Document(source="granola", native_id="g", title="t",
                                    messages=[Message(0, "note", "hello world")]))
        for q in ['"', ") OR (", "AND", "*", "", "hello AND"]:
            st.search(q)  # must not raise


class TestClaudeCode(unittest.TestCase):
    def test_parse_jsonl_session(self):
        d = Path(_TMP) / "cc" / "-Users-x-proj"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "sess.jsonl"
        lines = [
            {"type": "custom-title", "customTitle": "My Session", "sessionId": "sess"},
            {"type": "user", "sessionId": "sess", "cwd": "/Users/x/proj",
             "timestamp": "2026-07-01T09:00:00Z",
             "message": {"role": "user", "content": "search the codebase"}},
            {"type": "assistant", "timestamp": "2026-07-01T09:00:05Z",
             "message": {"role": "assistant",
                         "content": [{"type": "thinking", "thinking": "consider"},
                                     {"type": "text", "text": "here is the result"}]}},
            {"type": "attachment", "timestamp": "2026-07-01T09:01:00Z",
             "attachment": {"text": "pasted meeting note about roadmap"}},
        ]
        f.write_text("\n".join(json.dumps(x) for x in lines))
        st = fresh_store("cc")
        rep = claude_code.ingest(st, projects_dir=Path(_TMP) / "cc")
        self.assertEqual(rep.changed, 1)
        hits = st.search("roadmap")           # attachment text is searchable
        self.assertTrue(hits)
        self.assertEqual(hits[0]["project"], "proj")   # derived from cwd
        # title came from custom-title event
        self.assertEqual(st.get_document(hits[0]["doc_id"])["title"], "My Session")

        # second run is a no-op (signature unchanged)
        rep2 = claude_code.ingest(st, projects_dir=Path(_TMP) / "cc")
        self.assertEqual(rep2.changed, 0)


class TestClaudeAi(unittest.TestCase):
    def _export(self, name):
        box = Path(_TMP) / name
        box.mkdir(parents=True, exist_ok=True)
        convs = [{
            "uuid": "c1", "name": "Budget talk",
            "created_at": "2026-05-01T00:00:00Z", "updated_at": "2026-05-01T00:10:00Z",
            "chat_messages": [
                {"sender": "human", "text": "What is our Q3 budget?"},
                {"sender": "assistant", "content": [{"type": "text", "text": "Locked at plan level."}]},
            ],
        }]
        (box / "conversations.json").write_text(json.dumps(convs))
        return box

    def test_import_json_export(self):
        box = self._export("cai")
        st = fresh_store("cai")
        rep = claude_ai.ingest(st, inbox=box)
        self.assertEqual(rep.changed, 1)
        self.assertTrue(st.search("budget"))

    def test_import_zip_export(self):
        import zipfile
        box = self._export("caiz")
        (Path(_TMP) / "caiz" / "conversations.json").rename(Path(_TMP) / "caiz" / "conv_src.json")
        zpath = box / "export.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.write(Path(_TMP) / "caiz" / "conv_src.json", arcname="data/conversations.json")
        (Path(_TMP) / "caiz" / "conv_src.json").unlink()
        st = fresh_store("caiz")
        rep = claude_ai.ingest(st, inbox=box)
        self.assertEqual(rep.changed, 1)
        self.assertTrue(st.search("budget"))


class TestGranola(unittest.TestCase):
    def test_markdown_with_frontmatter(self):
        box = Path(_TMP) / "gm"
        box.mkdir(parents=True, exist_ok=True)
        (box / "note.md").write_text(
            "---\ntitle: Kickoff\ndate: 2026-06-15\nproject: acme\n---\n"
            "# Kickoff\n\n## Notes\nPricing anchor decided.\n\n## Transcript\nA: hi\nB: hey\n")
        st = fresh_store("gm")
        rep = granola.ingest(st, inbox=box)
        self.assertEqual(rep.changed, 1)
        hits = st.search("anchor", project="acme")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "Kickoff")

    def test_inbox_readme_and_scaffold_files_are_skipped(self):
        box = Path(_TMP) / "gskip"
        box.mkdir(parents=True, exist_ok=True)
        (box / "README.md").write_text("# Drop your Granola notes here\ninstructions")
        (box / "_notes.md").write_text("# ignore underscore-prefixed")
        (box / "real.md").write_text("# Real meeting\nactual content")
        st = fresh_store("gskip")
        rep = granola.ingest(st, inbox=box)
        self.assertEqual(rep.changed, 1)                       # only real.md
        self.assertFalse(st.search("instructions"))            # README not indexed
        self.assertTrue(st.search("actual"))

    def test_json_api_shape_splits_notes_and_transcript(self):
        obj = {"id": "g9", "title": "Sync", "created_at": "2026-05-01T09:00:00Z",
               "notes_markdown": "ship search first",
               "transcript": [{"speaker": "K", "text": "search is priority"}]}
        doc = granola.document_from_granola_json(obj)
        roles = [m.role for m in doc.messages]
        self.assertEqual(roles, ["note", "transcript"])

    def test_double_encoded_cache_parse(self):
        cache = {"cache": json.dumps(
            {"state": {"documents": {"g2": {"id": "g2", "title": "Budget", "summary": "Q3 locked"}}}})}
        docs = list(granola_live._documents_from_cache(json.dumps(cache).encode()))
        self.assertEqual([d["title"] for d in docs], ["Budget"])

    def test_safestorage_format_detection(self):
        # non-v10 blobs are rejected by the CBC path without crashing
        self.assertIsNone(granola_live._try_safestorage(b"key", b"\x2e\x48\xecrandom"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
