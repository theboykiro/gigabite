"""`gigabite welcome` — the last thing the installer prints, and the first thing a
non-engineer reads.

Three cases, because they are three different messages and only one of them is the
happy path:

  * a populated index — must name the user's own material and hand over a command
    that is *verified* to return something, since a suggested first search that
    finds nothing is worse than no suggestion at all;
  * an empty index — must say so plainly. The failure this guards against is a
    cheerful brief on a store with nothing in it;
  * no Claude Code — the slash commands and ambient recall do not exist, so
    advertising them promises a feature the user cannot use.

Read-only is asserted, not assumed. `Store.search` stamps `accessed_utc` and
un-archives what it matched by default, so the obvious implementation of the
verified-example step would quietly write to the index on every run.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import contextlib
import io
import json
import shutil
import unittest

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
from _harness import TempRoot, doc  # noqa: F401  (must precede any gigabite import)

from gigabite import cli, config  # noqa: E402
from gigabite.features import save  # noqa: E402
from gigabite.store import Store, connect  # noqa: E402


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


def out_lines(out: str, contains: str) -> list:
    return [line for line in out.splitlines() if contains in line]


class WelcomeCase(TempRoot):
    """One note-shaped project plus a Claude Code session, as a fresh install has.

    The Claude Code document is inserted through the store rather than written as
    a transcript: `welcome` reads the index, and building a realistic `.jsonl`
    session file would test the ingester instead.
    """

    def setUp(self):
        super().setUp()
        save.ensure_project("acme", keywords=["widget", "acme"])
        folder = self.root / "acme"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "2026-07-09-retro.md").write_text(
            "---\ntitle: Widget launch retro\ndate: 2026-07-09\n---\n\n"
            "The retro concluded that onboarding, not pricing, was the blocker.\n",
            encoding="utf-8")
        run("ingest")
        store = Store(connect(config.DB_PATH))
        store.upsert_document(doc(
            config.SOURCE_CLAUDE_CODE, "session-one",
            title="Widget pricing decision", project="acme",
            created="2026-07-08T09:00:00Z",
            texts=["Where did we land on widget pricing?",
                   "Anchored at the mid tier; the enterprise anchor scared off "
                   "the self-serve segment."]))
        store.commit()

    def store(self) -> Store:
        return Store(connect(config.DB_PATH))


class TestPopulatedIndex(WelcomeCase):
    def test_it_exits_zero_and_says_something(self):
        code, out = run("welcome")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_it_counts_in_plain_language_not_source_identifiers(self):
        _code, out = run("welcome")
        self.assertIn("1 conversation", out)
        self.assertIn("1 note", out)
        self.assertNotIn("claude_code", out)
        self.assertNotIn("word_count", out)

    def test_it_names_the_sources_and_the_date_span(self):
        _code, out = run("welcome")
        self.assertIn("Claude Code", out)
        self.assertIn("2026", out)
        self.assertIn("spanning", out)

    def test_the_suggested_command_is_a_search_that_actually_matches(self):
        """The whole point: their first search must not come back empty."""
        _code, out = run("welcome")
        line = next(l for l in out.splitlines() if "gigabite search" in l)
        query = line.split('"')[1]
        hits = self.store().search(query, record=False)
        self.assertTrue(hits, f"suggested query {query!r} returned nothing")

    def test_the_example_names_one_of_their_own_documents(self):
        _code, out = run("welcome")
        titles = {d["title"] for d in self.store().iter_documents()}
        self.assertTrue([t for t in titles if t in out],
                        "the example should show a real indexed title")

    def test_it_offers_the_claude_ai_export_only_while_it_is_missing(self):
        _code, out = run("welcome")
        self.assertIn("claude.ai", out)
        store = self.store()
        store.upsert_document(doc(config.SOURCE_CLAUDE_AI, "chat-one",
                                  title="Widget rollout chat",
                                  created="2026-07-10T09:00:00Z",
                                  texts=["Any risk in the rollout?"]))
        store.commit()
        _code, out = run("welcome")
        self.assertNotIn("Export data", out)

    def test_it_points_at_the_knowledge_root_for_manual_additions(self):
        _code, out = run("welcome")
        self.assertIn(str(self.root), out)

    def test_it_is_read_only(self):
        before = {d["doc_id"]: d["accessed_utc"] for d in self.store().iter_documents()}
        files_before = self.files()
        run("welcome")
        after = {d["doc_id"]: d["accessed_utc"] for d in self.store().iter_documents()}
        self.assertEqual(before, after, "welcome must not stamp accessed_utc")
        self.assertEqual(files_before, self.files())

    def test_it_is_rerunnable_with_the_same_answer(self):
        first = run("welcome")[1]
        self.assertEqual(first, run("welcome")[1])

    def test_it_does_not_duplicate_status(self):
        """Different question, different output. `status` prints the db path and
        raw per-source rows; this must not turn into a second copy of it."""
        _code, out = run("welcome")
        self.assertNotIn(str(config.DB_PATH), out)
        self.assertNotIn("words", out)


class TestEmptyIndex(TempRoot):
    """No Claude Code history, nothing in ~/Knowledge. Honesty is the requirement."""

    def test_it_says_the_index_is_empty_and_exits_zero(self):
        code, out = run("welcome")
        self.assertEqual(code, 0)
        self.assertIn("empty", out.lower())

    def test_it_does_not_claim_to_have_read_anything(self):
        _code, out = run("welcome")
        self.assertNotIn("already read your own work", out)
        self.assertNotIn("spanning", out)

    def test_it_gives_the_shortest_path_to_something_searchable(self):
        _code, out = run("welcome")
        self.assertIn("gigabite ingest", out)
        self.assertIn("gigabite search", out)
        self.assertIn(str(self.root), out)

    def test_it_creates_nothing_including_the_index_itself(self):
        """An unbuilt index is the honest answer to 'what is indexed'. Opening a
        store to find that out would create the database it is reporting absent."""
        self.assertFalse(config.DB_PATH.exists())
        run("welcome")
        self.assertFalse(config.DB_PATH.exists())
        self.assertEqual(self.files(), [])

    def test_unindexed_claude_code_history_is_not_reported_as_no_history(self):
        """Transcripts on disk with an empty index means `ingest` has not run —
        a different fact, and telling the user their own sessions do not exist is
        the one thing this command must never do."""
        session = config.CLAUDE_CODE_PROJECTS_DIR / "-Users-jane-widget" / "s1.jsonl"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text(json.dumps({
            "type": "user", "cwd": "/Users/jane/widget",
            "timestamp": "2026-07-08T09:00:00Z",
            "message": {"role": "user", "content": "Where did widget pricing land?"},
        }) + "\n", encoding="utf-8")
        _code, out = run("welcome")
        self.assertIn("gigabite ingest", out)
        self.assertNotIn("nothing to read", out)

    def test_an_index_that_exists_but_holds_nothing_reads_the_same(self):
        run("ingest")                       # builds an empty database
        self.assertTrue(config.DB_PATH.exists())
        code, out = run("welcome")
        self.assertEqual(code, 0)
        self.assertIn("empty", out.lower())


class TestNoUsableExample(TempRoot):
    """Titles too short to lift a query from. The first action still has to work.

    The fallback is `gigabite doc <id>`, which cannot miss — inventing a search
    term here would hand a new user a command that returns nothing, which is the
    failure the verified example exists to avoid.
    """

    def setUp(self):
        super().setUp()
        store = Store(connect(config.DB_PATH))
        store.upsert_document(doc(config.SOURCE_CLAUDE_CODE, "session-tiny",
                                  title="CI fix", created="2026-07-08T09:00:00Z",
                                  texts=["ok"]))
        store.commit()

    def test_it_falls_back_to_opening_a_real_document_by_id(self):
        _code, out = run("welcome")
        doc_ids = [d["doc_id"] for d in Store(connect(config.DB_PATH)).iter_documents()]
        self.assertIn("gigabite doc", out)
        self.assertTrue([i for i in doc_ids if i in out])

    def test_it_invents_no_search_term(self):
        """Every search it prints must be a placeholder the user fills in, never
        a concrete query nothing checked."""
        for line in out_lines(run("welcome")[1], "gigabite search"):
            self.assertIn("<", line, f"unverified query suggested: {line!r}")


class TestWithoutClaudeCode(WelcomeCase):
    """`~/.claude/projects` absent: no slash commands, no ambient recall.

    Removed rather than mocked — the harness points `CLAUDE_CODE_PROJECTS_DIR` at
    a temp directory, so deleting it is the real condition and not a stand-in.
    """

    def setUp(self):
        super().setUp()
        shutil.rmtree(config.CLAUDE_CODE_PROJECTS_DIR, ignore_errors=True)

    def test_it_does_not_advertise_the_slash_commands(self):
        _code, out = run("welcome")
        self.assertNotIn("/search", out)

    def test_it_does_not_promise_ambient_recall(self):
        _code, out = run("welcome")
        self.assertNotIn("as you type", out)

    def test_it_still_hands_over_a_working_first_command(self):
        _code, out = run("welcome")
        self.assertIn("gigabite search", out)
        self.assertIn(str(self.root), out)

    def test_the_populated_variant_does_advertise_them(self):
        """Guards the assertions above against passing for the wrong reason."""
        config.CLAUDE_CODE_PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        _code, out = run("welcome")
        self.assertIn("/search", out)

    def test_the_empty_case_also_drops_the_claude_code_promise(self):
        # pathlib's glob matches dot entries, so this takes the index with it.
        for path in self.root.glob("*"):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        self.assertFalse(config.DB_PATH.exists())
        _code, out = run("welcome")
        self.assertIn("empty", out.lower())
        self.assertNotIn("every Claude Code session", out)


if __name__ == "__main__":
    unittest.main()
