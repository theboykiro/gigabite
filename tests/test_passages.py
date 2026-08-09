"""Passage chunking and result diversity.

These use a synthetic corpus on purpose: the real index holds client and meeting
content, so nothing that could leak it belongs in a committed test. Behaviour
against the real corpus is measured separately with tools/eval_recall.py.
"""

import unittest
from pathlib import Path
import tempfile

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config, util  # noqa: E402
from gigabite.store import Document, Message, Store, connect, MAX_HITS_PER_DOC  # noqa: E402


def msg(seq, text, role="user", ts="2026-08-01T10:00:00+00:00"):
    return Message(seq=seq, role=role, text=text, ts_utc=ts)


class TestPassages(unittest.TestCase):
    def test_short_messages_are_merged(self):
        """A transcript's one-line turns should not each be their own row."""
        msgs = [msg(i, f"line number {i} of chatter") for i in range(40)]
        ps = util.passages(msgs, target=50, hard_max=100)
        self.assertLess(len(ps), len(msgs))
        for p in ps:
            self.assertLessEqual(util.word_count(p.text), 100)

    def test_long_message_is_split(self):
        """A 6,000-word meeting must not stay a single unrankable row."""
        big = "\n\n".join(f"Paragraph {i} " + " ".join(["word"] * 60) for i in range(20))
        ps = util.passages([msg(0, big)], target=180, hard_max=320)
        self.assertGreater(len(ps), 1)
        for p in ps:
            self.assertLessEqual(util.word_count(p.text), 320)

    def test_no_text_is_lost_or_duplicated(self):
        """Concatenating passages must reproduce every word, once."""
        msgs = [msg(0, "alpha beta gamma"), msg(1, "delta epsilon"), msg(2, "zeta")]
        ps = util.passages(msgs, target=3, hard_max=10)
        got = " ".join(p.text for p in ps).split()
        self.assertEqual(sorted(got), sorted("alpha beta gamma delta epsilon zeta".split()))

    def test_sentence_fallback_for_unpunctuated_run(self):
        """Transcripts often have no paragraph breaks; splitting must still work."""
        run = " ".join(["talking"] * 900)
        ps = util.passages([msg(0, run)], target=100, hard_max=200)
        self.assertGreater(len(ps), 4)
        for p in ps:
            self.assertLessEqual(util.word_count(p.text), 200)

    def test_passages_are_sequential_and_carry_provenance(self):
        msgs = [msg(0, "one two three"), msg(1, "four five six", role="assistant")]
        ps = util.passages(msgs, target=3, hard_max=10)
        self.assertEqual([p.seq for p in ps], list(range(len(ps))))
        self.assertEqual(ps[0].first_msg, 0)
        self.assertEqual(ps[0].role, "user")

    def test_empty_and_blank_input(self):
        self.assertEqual(util.passages([]), [])
        self.assertEqual(util.passages([msg(0, "   ")]), [])

    def test_granularity_is_evened_across_sources(self):
        """The actual fix: one long doc and one chatty doc become comparable rows."""
        meeting = [msg(0, " ".join(["discussion"] * 1800))]
        chat = [msg(i, "short turn here") for i in range(120)]
        mp = util.passages(meeting)
        cp = util.passages(chat)
        avg = lambda ps: sum(util.word_count(p.text) for p in ps) / len(ps)
        # Within 2x of each other, versus ~1800 vs ~3 words per row before.
        self.assertLess(max(avg(mp), avg(cp)) / min(avg(mp), avg(cp)), 2.0)


class TestSearchDiversity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = connect(Path(self.tmp.name) / "t.db")
        self.store = Store(self.conn)

    def _add(self, native_id, title, source, texts):
        doc = Document(
            source=source, native_id=native_id, title=title, project="p",
            created_utc="2026-08-01T00:00:00+00:00",
            updated_utc="2026-08-01T00:00:00+00:00",
            messages=[msg(i, t) for i, t in enumerate(texts)],
        )
        self.store.upsert_document(doc)
        self.store.commit()
        return doc

    def test_one_document_cannot_fill_the_page(self):
        # A hoggy document repeats the term across many well-separated passages.
        self._add("hog", "Hoggy session", config.SOURCE_CLAUDE_CODE,
                  [" ".join(["filler"] * 200) + " pomegranate" for _ in range(8)])
        for i in range(4):
            self._add(f"other{i}", f"Other {i}", config.SOURCE_NOTE,
                      ["pomegranate appears here too " + " ".join(["padding"] * 50)])

        rows = self.store.search("pomegranate", limit=6, record=False)
        counts = {}
        for r in rows:
            counts[r["doc_id"]] = counts.get(r["doc_id"], 0) + 1
        self.assertTrue(counts, "expected some results")
        self.assertLessEqual(max(counts.values()), MAX_HITS_PER_DOC)
        self.assertGreater(len(counts), 1, "results should span several documents")

    def test_cap_does_not_hide_a_document_that_is_the_only_match(self):
        self._add("only", "Only match", config.SOURCE_NOTE,
                  ["kumquat " + " ".join(["pad"] * 100) for _ in range(5)])
        rows = self.store.search("kumquat", limit=5, record=False)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["doc_id"], util_doc_id("note", "only"))

    def test_messages_survive_passage_indexing(self):
        """get_document must return the conversation as written, not passages."""
        doc = self._add("verbatim", "Verbatim", config.SOURCE_NOTE,
                        ["first message", "second message", "third message"])
        got = self.store.get_document(doc.doc_id)
        self.assertEqual([m["text"] for m in got["messages"]],
                         ["first message", "second message", "third message"])
        self.assertEqual([m["seq"] for m in got["messages"]], [0, 1, 2])
def util_doc_id(source, native_id):
    return util.doc_id(source, native_id)


if __name__ == "__main__":
    unittest.main()
