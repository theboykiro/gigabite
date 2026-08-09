"""Tests for recall quality: stop-word handling and transcript de-weighting.

Pure stdlib (unittest). No network; the index is a temp SQLite file.

Two defects are covered here, both of which made a phrased question retrieve the
user's own transcript instead of the material that answers it:

  A. Stop words were *required* terms in the AND pass, so "what did X say" asked
     FTS5 for messages containing "what" AND "did" AND "say".
  B. Claude Code transcripts quote the question (and any tool output) verbatim, so
     they are dense near-perfect matches for the questions about to be asked.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import tempfile
import unittest
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config, util  # noqa: E402
from gigabite import store as store_mod  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402


class TestStopWords(unittest.TestCase):
    def test_and_pass_drops_stopwords(self):
        self.assertEqual(
            util.to_fts_query("what did jane say", "AND"),
            '"jane" "say"',
        )

    def test_or_pass_keeps_stopwords(self):
        """OR can only add candidates, so filtering there buys nothing."""
        self.assertIn('"what"', util.to_fts_query("what did jane say", "OR"))

    def test_all_stopword_query_is_not_emptied(self):
        """Filtering must never turn a real query into an empty match."""
        got = util.to_fts_query("what did they do", "AND")
        self.assertTrue(got.strip(), "query collapsed to nothing")
        self.assertIn('"what"', got)
    def test_prefix_star_survives_filtering(self):
        self.assertEqual(util.to_fts_query("what about pricing*", "AND"), '"pricing"*')

    def test_explicit_override_keeps_stopwords(self):
        self.assertIn(
            '"what"', util.to_fts_query("what did jane say", "AND", drop_stopwords=False)
        )


class _IndexBase(unittest.TestCase):
    """An index holding the same text under two different sources."""

    TEXT = "the pricing anchor was agreed with the client at nine seats"

    def setUp(self):
        self.db = _harness.SCRATCH / f"{self.id().split('.')[-1]}.db"
        if self.db.exists():
            self.db.unlink()
        self.st = Store(connect(self.db))
        for src, native in ((config.SOURCE_NOTE, "n1"),
                            (config.SOURCE_CLAUDE_CODE, "c1")):
            self.st.upsert_document(Document(
                source=src, native_id=native, title="Pricing", project="acme",
                messages=[Message(0, "note", self.TEXT)]))
        self.st.commit()
        self._orig = store_mod.TRANSCRIPT_RANK_PENALTY

    def tearDown(self):
        store_mod.TRANSCRIPT_RANK_PENALTY = self._orig

    def sources_in_rank_order(self, query):
        out, seen = [], set()
        for r in self.st.search(query, limit=10, record=False):
            if r["doc_id"] in seen:
                continue
            seen.add(r["doc_id"])
            out.append(r["source"])
        return out


class TestTranscriptDeweighting(_IndexBase):
    def test_transcript_ranks_below_an_equally_matching_source(self):
        ranked = self.sources_in_rank_order("pricing anchor")
        self.assertEqual(ranked[0], config.SOURCE_NOTE)
        self.assertIn(config.SOURCE_CLAUDE_CODE, ranked)
    def test_transcript_still_wins_when_it_is_the_only_match(self):
        self.st.upsert_document(Document(
            source=config.SOURCE_CLAUDE_CODE, native_id="c2", title="Only",
            project="acme",
            messages=[Message(0, "note", "we decided to defer the loyalty rollout")]))
        self.st.commit()
        ranked = self.sources_in_rank_order("defer loyalty rollout")
        self.assertEqual(ranked[0], config.SOURCE_CLAUDE_CODE)
    def test_the_penalty_applies_only_to_transcripts(self):
        """A note's score must be identical with the penalty on and off.

        The earlier version of this asserted `penalised == raw * 0.30`, which
        restated the implementation's multiplication: moving de-weighting into the
        SQL ORDER BY, or to a rank-position adjustment, would have failed it while
        user-visible ranking was unchanged. What actually matters is that the
        penalty is narrow — it must not quietly touch source material.
        """
        store_mod.TRANSCRIPT_RANK_PENALTY = 1.0
        raw = {r["source"]: r["score"]
               for r in self.st.search("pricing anchor", limit=10, record=False)}
        store_mod.TRANSCRIPT_RANK_PENALTY = 0.30
        pen = {r["source"]: r["score"]
               for r in self.st.search("pricing anchor", limit=10, record=False)}
        self.assertAlmostEqual(pen[config.SOURCE_NOTE], raw[config.SOURCE_NOTE], places=6)

    def test_project_and_source_filters_still_work_with_the_penalty(self):
        """The penalty binds params in SELECT, ahead of WHERE — easy to break."""
        rows = self.st.search("pricing anchor", project="acme",
                              sources=[config.SOURCE_NOTE], limit=10, record=False)
        self.assertTrue(rows)
        self.assertTrue(all(r["source"] == config.SOURCE_NOTE for r in rows))
        self.assertTrue(all(r["project"] == "acme" for r in rows))

    def test_wrong_project_returns_nothing(self):
        self.assertEqual(
            self.st.search("pricing anchor", project="nope", limit=10, record=False), [])


class TestPhrasedQuestionFindsTheSource(_IndexBase):
    """The end-to-end regression: A and B together."""

    def test_phrased_question_reaches_the_non_transcript_source(self):
        ranked = self.sources_in_rank_order("what was the pricing anchor agreed at")
        self.assertTrue(ranked, "phrased question retrieved nothing at all")
        self.assertEqual(ranked[0], config.SOURCE_NOTE)


class TestRouteContext(_IndexBase):
    """`route` powers /gg and the ambient recall hook, and had no test at all.

    A pasted shell prompt carries `@Janes-MacBook-Pro`, which was accepted as an
    explicit project marker. Recall then scoped the search to a project that holds
    nothing, and reported the laptop back to the user as the detected context.
    """

    def setUp(self):
        super().setUp()
        (config.KNOWLEDGE_DIR / "acme").mkdir(parents=True, exist_ok=True)
        (config.KNOWLEDGE_DIR / "acme" / "_project.md").write_text(
            "---\nproject: acme\nkeywords: pricing, anchor\nlayers: \n---\n",
            encoding="utf-8")

    def test_marker_for_a_nonexistent_project_is_not_the_context(self):
        from gigabite.features import routing
        out = routing.route(self.st, "janedoe@Janes-MacBook-Pro pricing anchor")
        self.assertNotEqual(out["context"]["project"], "Janes-MacBook-Pro")
        # falls through to keywords, which name the project that does exist
        self.assertEqual(out["context"]["project"], "acme")
        self.assertTrue(out["hits"], "recall returned nothing")
