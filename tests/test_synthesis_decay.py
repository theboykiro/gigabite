"""Tests for reference-frequency decay and gated daily synthesis.

Pure stdlib (unittest). No network, no writes to the real home directory.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Redirect all stores into a temp dir BEFORE importing the package.
_TMP = tempfile.mkdtemp(prefix="gigabite-syn-test-")
os.environ["GIGABITE_CORE_DIR"] = str(Path(_TMP) / "core")
os.environ["GIGABITE_KNOWLEDGE_DIR"] = str(Path(_TMP) / "knowledge")

from gigabite import config  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402
from gigabite.features import decay, synthesis  # noqa: E402


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def fresh_store(name) -> Store:
    db = Path(_TMP) / f"{name}.db"
    if db.exists():
        db.unlink()
    return Store(connect(db))


def _doc(native_id, title, updated_days_ago, project="", text="content here"):
    iso = _iso_days_ago(updated_days_ago)
    return Document(
        source="claude_code", native_id=native_id, title=title, project=project,
        created_utc=iso, updated_utc=iso,
        messages=[Message(0, "user", text)],
    )


class TestDecay(unittest.TestCase):
    def test_dry_run_does_not_change_state(self):
        st = fresh_store("decay_dry")
        st.upsert_document(_doc("stale", "Stale doc", updated_days_ago=60))
        st.upsert_document(_doc("fresh", "Fresh doc", updated_days_ago=1))
        st.commit()

        report = decay.run(st, window_days=30, dry_run=True)
        ids = [a["doc_id"] for a in report["archived"]]
        self.assertEqual(len(ids), 1)
        self.assertTrue(report["dry_run"])
        # nothing actually archived
        self.assertTrue(all(d["active"] for d in st.iter_documents(include_historical=True)))

    def test_archives_only_stale_documents(self):
        st = fresh_store("decay_run")
        stale = _doc("stale", "Stale doc", updated_days_ago=60)
        fresh = _doc("fresh", "Fresh doc", updated_days_ago=1)
        st.upsert_document(stale)
        st.upsert_document(fresh)
        st.commit()

        report = decay.run(st, window_days=30, dry_run=False)
        self.assertEqual([a["doc_id"] for a in report["archived"]], [stale.doc_id])

        active = {d["doc_id"] for d in st.iter_documents(include_historical=False)}
        self.assertIn(fresh.doc_id, active)
        self.assertNotIn(stale.doc_id, active)

    def test_undateable_document_is_left_active(self):
        st = fresh_store("decay_undated")
        st.upsert_document(Document(
            source="granola", native_id="nodate", title="No timestamps",
            messages=[Message(0, "note", "floating note")]))
        st.commit()
        report = decay.run(st, window_days=30, dry_run=False)
        self.assertEqual(report["archived"], [])

    def test_record_access_restores_archived_document(self):
        st = fresh_store("decay_reaccess")
        stale = _doc("stale", "Stale doc", updated_days_ago=60, text="quantum widgets")
        st.upsert_document(stale)
        st.commit()
        decay.run(st, window_days=30, dry_run=False)

        # archived -> excluded from default search
        self.assertFalse(st.search("quantum widgets"))
        # re-access restores it
        st.record_access([stale.doc_id])
        active = {d["doc_id"] for d in st.iter_documents(include_historical=False)}
        self.assertIn(stale.doc_id, active)
        self.assertTrue(st.search("quantum widgets"))

    def test_search_on_historical_restores_via_record_access(self):
        st = fresh_store("decay_search_restore")
        stale = _doc("stale", "Stale doc", updated_days_ago=60, text="reticulating splines")
        st.upsert_document(stale)
        st.commit()
        decay.run(st, window_days=30, dry_run=False)

        # explicit historical search records access -> restores
        hits = st.search("reticulating splines", include_historical=True, record=True)
        self.assertTrue(hits)
        active = {d["doc_id"] for d in st.iter_documents(include_historical=False)}
        self.assertIn(stale.doc_id, active)

    def test_restore_and_status(self):
        st = fresh_store("decay_status")
        stale = _doc("stale", "Stale doc", updated_days_ago=60)
        fresh = _doc("fresh", "Fresh doc", updated_days_ago=1)
        st.upsert_document(stale)
        st.upsert_document(fresh)
        st.commit()
        decay.run(st, window_days=30, dry_run=False)

        status = decay.status(st)
        self.assertEqual(status["active"], 1)
        self.assertEqual(status["archived"], 1)

        self.assertTrue(decay.restore(st, stale.doc_id))
        self.assertFalse(decay.restore(st, "claude_code:doesnotexist"))
        self.assertEqual(decay.status(st)["active"], 2)


class TestSynthesis(unittest.TestCase):
    def test_digest_collects_recent_only(self):
        st = fresh_store("syn_digest")
        st.upsert_document(_doc("recent", "Recent chat", updated_days_ago=0,
                                project="acme", text="decided to anchor pricing"))
        st.upsert_document(_doc("old", "Old chat", updated_days_ago=10,
                                project="acme", text="ancient history"))
        st.commit()

        digest = synthesis.build_digest(st, since_days=1)
        self.assertEqual(digest["document_count"], 1)
        titles = [
            e["title"]
            for g in digest["groups"]
            for e in g["documents"]
        ]
        self.assertEqual(titles, ["Recent chat"])

    def test_write_proposal_is_gated_and_never_touches_core(self):
        st = fresh_store("syn_proposal")
        st.upsert_document(_doc("recent", "Recent chat", updated_days_ago=0,
                                project="acme", text="new constraint discovered"))
        st.commit()

        path = synthesis.write_proposal(st, since_days=1)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, config.KNOWLEDGE_DIR / "_proposals")

        body = path.read_text(encoding="utf-8")
        self.assertIn("Proposed knowledge updates", body)
        self.assertIn("Proposed core.md updates", body)
        self.assertIn("GATED", body)
        self.assertIn("Recent chat", body)

        # the gate: core.md is never created or written
        self.assertFalse(config.CORE_FILE.exists())

    def test_write_proposal_is_idempotent_for_the_day(self):
        st = fresh_store("syn_idempotent")
        st.upsert_document(_doc("recent", "Recent chat", updated_days_ago=0))
        st.commit()

        p1 = synthesis.write_proposal(st, since_days=1)
        p2 = synthesis.write_proposal(st, since_days=1)
        self.assertEqual(p1, p2)
        self.assertEqual(len(synthesis.list_proposals()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
