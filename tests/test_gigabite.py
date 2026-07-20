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
from gigabite.sources import claude_ai, claude_ai_live, claude_code, granola, granola_live  # noqa: E402


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

    def test_or_fallback_when_and_is_empty(self):
        st = fresh_store("orfb")
        st.upsert_document(Document(
            source="claude_code", native_id="o1", title="FTS notes",
            messages=[Message(0, "user", "sqlite fts5 works well with bm25")]))
        # "ranking" doesn't co-occur, so strict AND is empty -> OR fallback finds it
        self.assertFalse(st._run_match(util.to_fts_query("sqlite fts5 ranking", "AND"),
                                       None, None, 20))
        hits = st.search("sqlite fts5 ranking")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "FTS notes")

    def test_restore_on_access_via_historical_fallback(self):
        st = fresh_store("restore")
        st.upsert_document(Document(source="granola", native_id="arch", title="Archived meeting",
                                    messages=[Message(0, "note", "the wombat migration plan")]))
        did = util.doc_id("granola", "arch")
        st.set_active(did, False)
        # default search excludes it (decay working)
        self.assertFalse(st.search("wombat"))
        # historical search returns it AND restores it (record_access -> active=1)
        hits = st.search("wombat", include_historical=True)
        self.assertTrue(hits)
        row = st.conn.execute("SELECT active FROM documents WHERE doc_id=?", (did,)).fetchone()
        self.assertEqual(row["active"], 1)
        # now it's active again for default search
        self.assertTrue(st.search("wombat"))

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

    def test_shared_sessionid_files_do_not_collide_and_agents_skipped(self):
        d = Path(_TMP) / "cc2" / "-Users-x-proj"
        d.mkdir(parents=True, exist_ok=True)
        # two files that both carry the SAME sessionId in their events, each with
        # a token unique to that file
        markers = {"main-uuid": "quokkamarker", "agent-sub1": "narwhalmarker"}
        for stem, marker in markers.items():
            (d / f"{stem}.jsonl").write_text("\n".join(json.dumps(x) for x in [
                {"type": "user", "sessionId": "SHARED", "cwd": "/Users/x/proj",
                 "message": {"role": "user", "content": f"unique {marker} here"}},
            ]))
        st = fresh_store("cc2")
        rep = claude_code.ingest(st, projects_dir=Path(_TMP) / "cc2")
        # agent-*.jsonl is skipped; only the real session is indexed -> no collision
        self.assertEqual(rep.scanned, 1)
        self.assertEqual(rep.changed, 1)
        self.assertTrue(st.search("quokkamarker"))       # real session indexed
        self.assertFalse(st.search("narwhalmarker"))     # agent transcript not indexed


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


class TestClaudeAiLive(unittest.TestCase):
    """Exercise the live pull with the network stubbed out."""

    FAKE = {
        "/organizations": [{"uuid": "org1", "name": "Personal"}],
        "/organizations/org1/projects": [{"uuid": "proj1", "name": "Acme"}],
        "/organizations/org1/chat_conversations": [
            {"uuid": "conv-free", "name": "Loose chat", "updated_at": "2026-06-01T00:00:00Z"},
            {"uuid": "conv-proj", "name": "Acme chat", "updated_at": "2026-06-02T00:00:00Z",
             "project_uuid": "proj1"},
        ],
    }
    DETAIL = {
        "conv-free": {"uuid": "conv-free", "name": "Loose chat",
                      "chat_messages": [{"sender": "human", "text": "standalone question about widgets"}]},
        "conv-proj": {"uuid": "conv-proj", "name": "Acme chat",
                      "chat_messages": [{"sender": "assistant",
                                         "content": [{"type": "text", "text": "project-scoped answer"}]}]},
    }

    def _fake_get(self, path, token):
        if "/chat_conversations/" in path:
            uuid = path.split("/chat_conversations/")[1].split("?")[0]
            return self.DETAIL[uuid]
        return self.FAKE[path]

    def setUp(self):
        self._orig = claude_ai_live._get
        claude_ai_live._get = self._fake_get

    def tearDown(self):
        claude_ai_live._get = self._orig

    def test_pulls_projectless_and_project_chats(self):
        st = fresh_store("live")
        rep = claude_ai_live.ingest(st, token="fake-token")
        self.assertEqual(rep.changed, 2)

        # projectless chat has no project; project chat is tagged with the project name
        free = st.search("widgets")
        self.assertTrue(free)
        self.assertEqual(free[0]["project"], "")
        proj = st.search("project-scoped")
        self.assertTrue(proj)
        self.assertEqual(proj[0]["project"], "Acme")

        # second run is fully incremental (updated_at signatures unchanged)
        rep2 = claude_ai_live.ingest(st, token="fake-token")
        self.assertEqual(rep2.changed, 0)
        self.assertEqual(rep2.skipped, 2)

    def test_no_token_is_a_clean_noop(self):
        st = fresh_store("live2")
        claude_ai_live._get = self._orig               # ensure real API path not hit
        orig_read = claude_ai_live.read_token
        claude_ai_live.read_token = lambda: None       # hermetic: ignore machine keychain
        try:
            rep = claude_ai_live.ingest(st, token=None)
        finally:
            claude_ai_live.read_token = orig_read
        self.assertEqual(rep.changed, 0)
        self.assertEqual(rep.errors, [])
        self.assertTrue(any("no claude.ai token" in n for n in rep.notes))


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

    def test_nested_reserved_dir_is_skipped(self):
        box = Path(_TMP) / "gnest"
        (box / "_archive").mkdir(parents=True, exist_ok=True)
        (box / "_archive" / "old.md").write_text("# Old\nzebrafishmarker content")
        (box / "live.md").write_text("# Live\ndolphinmarker content")
        st = fresh_store("gnest")
        granola.ingest(st, inbox=box)
        self.assertTrue(st.search("dolphinmarker"))
        self.assertFalse(st.search("zebrafishmarker"))   # nested under _archive -> skipped

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
