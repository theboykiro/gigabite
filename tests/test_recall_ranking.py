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


class TestPhraseMatching(unittest.TestCase):
    """Adjacency is what makes a remembered span identifying.

    Quoting each word separately and OR-ing them searches for the span's
    commonest words, which is a different question with a different answer.
    """

    SPAN = "the migration window closes before the second billing cycle"

    def setUp(self):
        self.st = _harness.scratch_store("phrase_match")
        # The decoy holds every word of the span, densely and out of order, and
        # nothing else — so it wins on OR'd terms. It never puts them in that
        # order, so it cannot match the span itself.
        decoy = " ".join(["billing cycle migration window closes second"] * 6)
        for i in range(8):                      # a corpus, so bm25 has real IDF
            self.st.upsert_document(Document(
                source=config.SOURCE_NOTE, native_id=f"filler{i}", title=f"Other {i}",
                messages=[Message(0, "note", f"unrelated note {i} on staffing and leave")]))
        self.st.upsert_document(Document(
            source=config.SOURCE_NOTE, native_id="decoy", title="Terms",
            messages=[Message(0, "note", decoy)]))
        # The source states the span once, buried in ordinary prose, which is
        # what a real document looks like and what bm25 penalises on length.
        filler = " ".join(f"paragraph {i} of the cutover plan covering rollout steps"
                          for i in range(10))
        self.st.upsert_document(Document(
            source=config.SOURCE_NOTE, native_id="source", title="Cutover",
            messages=[Message(0, "note", f"{filler}. {self.SPAN}. {filler}")]))
        self.st.commit()

    def _top(self, query):
        rows = self.st.search(query, limit=10, record=False)
        return rows[0]["doc_id"] if rows else None

    def test_phrase_query_retrieves_its_source(self):
        want = util.doc_id(config.SOURCE_NOTE, "source")
        self.assertEqual(self._top(self.SPAN), want,
                         "contiguous span did not retrieve the document it came from")

    def test_the_or_ladder_alone_would_have_missed_it(self):
        """Pins the premise: without adjacency the decoy wins, so the pass earns its place."""
        or_match = util.to_fts_query(self.SPAN, "OR")
        rows = self.st._run_match(or_match, None, None, 10)
        self.assertEqual(rows[0]["doc_id"], util.doc_id(config.SOURCE_NOTE, "decoy"))

    def test_bag_query_still_works(self):
        """The fallback ladder must survive. Out-of-order words are never adjacent."""
        self.assertEqual(self._top("cutover rollout steps paragraph"),
                         util.doc_id(config.SOURCE_NOTE, "source"))

    def test_short_query_still_works(self):
        self.assertEqual(self._top("cutover"), util.doc_id(config.SOURCE_NOTE, "source"))

    def test_partly_remembered_span_falls_back_rather_than_returning_nothing(self):
        """One wrong word breaks the phrase; the ladder still has to answer."""
        rows = self.st.search("the migration window shuts before the second billing cycle",
                              limit=10, record=False)
        self.assertTrue(rows, "a near-miss span returned nothing at all")


class TestPhraseQuerySyntax(unittest.TestCase):
    def test_phrase_is_one_quoted_span(self):
        self.assertEqual(util.to_fts_phrase("migration window closes"),
                         '"migration window closes"')

    def test_single_token_has_no_phrase(self):
        self.assertEqual(util.to_fts_phrase("migration"), "")
        self.assertEqual(util.to_fts_phrase(""), "")

    def test_punctuation_and_quotes_cannot_break_the_match(self):
        """Every span must be a legal MATCH expression, or search raises."""
        st = _harness.scratch_store("phrase_syntax")
        st.upsert_document(Document(
            source=config.SOURCE_NOTE, native_id="n1", title="T",
            messages=[Message(0, "note", "some ordinary content")]))
        st.commit()
        for hostile in ('he said "yes" then NOT no',
                        'a OR b AND (c) -- ;drop',
                        'quote " unbalanced',
                        '*(){}[]^:"',
                        "it's a near-miss span"):
            with self.subTest(hostile):
                st.search(hostile, limit=5, record=False)   # must not raise


class TestArchivedFallback(unittest.TestCase):
    """Decay's promise: archived material is reachable when nothing active matches."""

    def setUp(self):
        self.st = _harness.scratch_store("archived_fallback")
        self.st.upsert_document(Document(
            source=config.SOURCE_NOTE, native_id="old", title="Retired",
            messages=[Message(0, "note", "the seasonal surcharge model we retired")]))
        self.st.commit()
        self.old = util.doc_id(config.SOURCE_NOTE, "old")
        self.st.set_active(self.old, False)

    def test_archived_document_is_returned_when_nothing_active_matches(self):
        rows = self.st.search("seasonal surcharge model", limit=5, record=False)
        self.assertTrue(rows, "archived document was unreachable at any rank")
        self.assertEqual(rows[0]["doc_id"], self.old)

    def test_active_documents_outrank_archived_ones(self):
        self.st.upsert_document(Document(
            source=config.SOURCE_NOTE, native_id="new", title="Current",
            messages=[Message(0, "note", "the seasonal surcharge model we use now")]))
        self.st.commit()
        rows = self.st.search("seasonal surcharge model", limit=5, record=False)
        self.assertEqual(rows[0]["doc_id"], util.doc_id(config.SOURCE_NOTE, "new"))
        # decay's point: an active answer means archived material stays archived
        self.assertNotIn(self.old, {r["doc_id"] for r in rows})

    def test_returning_an_archived_document_restores_it(self):
        rows = self.st.search("seasonal surcharge model", limit=5)   # record=True
        self.assertTrue(rows)
        self.assertTrue(self.st.get_document(self.old)["active"])

    def test_record_false_leaves_it_archived(self):
        self.st.search("seasonal surcharge model", limit=5, record=False)
        self.assertFalse(self.st.get_document(self.old)["active"])


class TestEvalHarnessTargets(unittest.TestCase):
    """The yardstick has to be answerable, or ranking is tuned against noise.

    `build_queries` used to pick targets from `documents` with no `active`
    filter, so it generated questions whose answer decay had already put out of
    the default search's reach — every headline figure came out ~32 points low
    and the number moved with the decay job rather than with the ranking.
    """

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import eval_recall
        self.eval_recall = eval_recall
        self.st = _harness.scratch_store("eval_targets")
        words = "quarterly rebate schedule renegotiated warehouse throughput"
        for native, extra in (("live", "kept current"), ("stale", "left untouched")):
            self.st.upsert_document(Document(
                source=config.SOURCE_NOTE, native_id=native, title=f"Doc {native}",
                messages=[Message(0, "note", f"{words} {extra}. {words} again {extra}.")]))
        self.st.commit()
        self.archived = util.doc_id(config.SOURCE_NOTE, "stale")
        self.st.set_active(self.archived, False)

    def test_no_query_targets_an_archived_document(self):
        targets = {q["expect"] for q in self.eval_recall.build_queries(self.st.conn)}
        self.assertNotIn(self.archived, targets)
        self.assertIn(util.doc_id(config.SOURCE_NOTE, "live"), targets)

    def test_archived_targets_are_available_on_request(self):
        """Measuring the decay fallback deliberately is a separate, opt-in run."""
        targets = {q["expect"]
                   for q in self.eval_recall.build_queries(self.st.conn,
                                                           archived_targets=True)}
        self.assertIn(self.archived, targets)
