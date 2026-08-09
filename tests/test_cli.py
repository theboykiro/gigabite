"""Characterisation tests for the command line — the surface users actually touch.

Until now every CLI command was untested. That mattered more than it sounds,
because the shipped slash commands are thin wrappers around these:

    /gg             -> core, ingest --no-remote, route --json
    /search         -> ingest, search
    /recall-status  -> ingest, status
    /calendar       -> calendar add --json-file, calendar agenda --day today

`Store.search` had good coverage; `gigabite search` had none, so argument parsing,
filters, output shape and exit codes were all unverified.

These are **characterisation** tests: they pin what the commands do today so that
the changes queued in ROADMAP item 8 — taking the inline ingest out of `/gg`,
stopping index writes on the recall path — are refactors with a safety net rather
than rewrites of untested code.

The most load-bearing tests here are `TestRouteJsonContract`. `install/hooks/gg-recall.sh`
parses `route --json` and reads exactly six keys off each hit. It also swallows every
error and exits 0, by design, so that a failure can never break a prompt — which
means that if one of those keys is ever renamed, ambient recall stops working
**silently and permanently**, with no error anywhere. Those tests are the only thing
standing between a rename and that outcome.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import contextlib
import io
import json
import re
import unittest

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
from _harness import TempRoot, doc  # noqa: F401  (must precede any gigabite import)

from gigabite import cli, config  # noqa: E402
from gigabite.features import save  # noqa: E402
from gigabite.store import Store, connect  # noqa: E402


def run(*argv):
    """Invoke the CLI as a user would. Returns (exit_code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class CliTestCase(TempRoot):
    """A corpus with realistic bm25 statistics in a private knowledge root.

    The filler documents are load-bearing, not padding. bm25 scores depend on how
    rare a term is across the corpus, so in a two-document index every score sits
    near zero and nothing clears the recall hook's `-1.0` threshold — the first
    version of this fixture failed `test_at_least_one_hit_clears_the_hook_threshold`
    for exactly that reason. With twenty ordinary notes around them the signal
    documents score around -16, which is what the real index looks like.
    """

    SIGNAL = {
        "2026-07-08-pricing.md": (
            "Widget pricing decision",
            "We anchored Widget pricing at the mid tier because the enterprise "
            "anchor was scaring off the self-serve segment."),
        "2026-07-09-retro.md": (
            "Widget launch retro",
            "The launch retro concluded that onboarding, not pricing, was the "
            "blocker for the self-serve segment."),
    }

    def setUp(self):
        super().setUp()
        save.ensure_project("acme", keywords=["widget", "acme"])
        folder = self.root / "acme"
        folder.mkdir(parents=True, exist_ok=True)
        for name, (title, body) in self.SIGNAL.items():
            (folder / name).write_text(
                f"---\ntitle: {title}\ndate: {name[:10]}\n---\n\n{body}\n",
                encoding="utf-8")
        for i in range(20):
            (folder / f"2026-06-{i + 1:02d}-standup.md").write_text(
                f"---\ntitle: Standup {i}\ndate: 2026-06-{i + 1:02d}\n---\n\n"
                f"Routine standup notes on delivery, staffing and the release "
                f"train, entry {i}.\n",
                encoding="utf-8")
        run("ingest")

    def store(self) -> Store:
        return Store(connect(config.DB_PATH))


# ---------------------------------------------------------------------------
# the hook contract
# ---------------------------------------------------------------------------

class TestRouteJsonContract(CliTestCase):
    """The six keys install/hooks/gg-recall.sh reads. Rename one and recall dies."""

    def payload(self, *prompt):
        code, out = run("route", "--json", *prompt)
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_top_level_shape(self):
        d = self.payload("what", "did", "we", "decide", "about", "widget", "pricing")
        self.assertIsInstance(d.get("hits"), list)
        self.assertIsInstance(d.get("context"), dict)
        self.assertIn("project", d["context"])
        self.assertIn("confidence", d["context"])

    def test_every_key_the_hook_reads_is_present(self):
        d = self.payload("what", "did", "we", "decide", "about", "widget", "pricing")
        self.assertTrue(d["hits"], "expected the corpus to match this prompt")
        for hit in d["hits"]:
            for key in ("score", "doc_id", "title", "source", "created_utc", "snippet"):
                self.assertIn(key, hit, f"gg-recall.sh reads {key!r} off every hit")

    def test_scores_are_negative_numbers(self):
        """The hook's strong-hit filter is `score < -1.0`. Flip the sign convention
        and every hit is silently discarded, with no error anywhere."""
        d = self.payload("widget", "pricing", "anchor", "decision")
        scores = [h["score"] for h in d["hits"]]
        self.assertTrue(scores)
        for s in scores:
            self.assertIsInstance(s, (int, float))
            self.assertLess(s, 0)

    def test_at_least_one_hit_clears_the_hook_threshold(self):
        """A canary on the -1.0 cut-off. If ranking drifts such that nothing ever
        clears it, ambient recall is dead and nothing else in the suite notices."""
        d = self.payload("widget", "pricing", "anchor", "decision")
        self.assertTrue([h for h in d["hits"] if h["score"] < -1.0])

    def test_json_is_a_single_line(self):
        """The hook captures stdout into a shell variable and json.loads it once."""
        _code, out = run("route", "--json", "widget", "pricing", "anchor")
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_a_prompt_that_matches_nothing_still_returns_valid_json(self):
        d = self.payload("xylophone", "quarterly", "bandersnatch")
        self.assertEqual(d["hits"], [])

    def test_limit_is_respected(self):
        d = json.loads(run("route", "--json", "--limit", "1", "widget", "pricing")[1])
        self.assertLessEqual(len(d["hits"]), 1)

    def test_human_output_names_the_context_and_the_hits(self):
        code, out = run("route", "widget", "pricing", "anchor")
        self.assertEqual(code, 0)
        self.assertIn("context:", out)


# ---------------------------------------------------------------------------
# search — the primary command
# ---------------------------------------------------------------------------

class TestSearch(CliTestCase):
    def test_finds_a_document_and_prints_its_title(self):
        code, out = run("search", "pricing", "anchor")
        self.assertEqual(code, 0)
        self.assertIn("Widget pricing decision", out)

    def test_json_is_a_list_of_hits_with_the_expected_keys(self):
        code, out = run("search", "--json", "pricing", "anchor")
        self.assertEqual(code, 0)
        hits = json.loads(out)
        self.assertIsInstance(hits, list)
        self.assertTrue(hits)
        for key in ("doc_id", "title", "source", "score", "snippet"):
            self.assertIn(key, hits[0])

    def test_no_match_exits_zero_and_says_so(self):
        """Not finding anything is an answer, not a failure."""
        code, out = run("search", "bandersnatch", "xylophone")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_limit_caps_the_result_count(self):
        hits = json.loads(run("search", "--json", "--limit", "1", "widget")[1])
        self.assertEqual(len(hits), 1)

    def test_project_filter_excludes_other_projects(self):
        hits = json.loads(run("search", "--json", "--project", "nosuchproject", "widget")[1])
        self.assertEqual(hits, [])

    def test_source_filter_excludes_other_sources(self):
        hits = json.loads(run("search", "--json", "--source", "granola", "widget")[1])
        self.assertEqual(hits, [])

    def test_a_query_with_fts_punctuation_does_not_crash(self):
        """Users type quotes and parentheses. FTS5 syntax errors must not surface."""
        for query in ('"unbalanced', "paren)", "AND", "a OR b", "*"):
            code, _out = run("search", query)
            self.assertEqual(code, 0, f"query {query!r} should not fail")


# ---------------------------------------------------------------------------
# ingest, status, doc
# ---------------------------------------------------------------------------

class TestIngest(CliTestCase):
    def test_reports_what_it_indexed(self):
        code, out = run("ingest")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_a_second_run_adds_nothing(self):
        before = len(self.store().iter_documents())
        run("ingest")
        self.assertEqual(len(self.store().iter_documents()), before)

    def test_source_filter_is_accepted(self):
        self.assertEqual(run("ingest", "--source", "note")[0], 0)

    def test_a_new_file_is_picked_up(self):
        (self.root / "acme" / "2026-07-10-hiring.md").write_text(
            "---\ntitle: Hiring plan\ndate: 2026-07-10\n---\n\nTwo engineers in Q3.\n",
            encoding="utf-8")
        run("ingest")
        self.assertIn("Hiring plan", run("search", "hiring", "plan")[1])


class TestStatus(CliTestCase):
    def test_reports_a_non_empty_index(self):
        code, out = run("status")
        self.assertEqual(code, 0)
        self.assertTrue(re.search(r"\d", out), "status should report counts")

    def test_json_is_parseable(self):
        code, out = run("status", "--json")
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(out), dict)


class TestDoc(CliTestCase):
    def test_prints_a_document_by_id(self):
        doc_id = json.loads(run("search", "--json", "pricing", "anchor")[1])[0]["doc_id"]
        code, out = run("doc", doc_id)
        self.assertEqual(code, 0)
        self.assertIn("anchor", out.lower())

    def test_an_unknown_id_exits_nonzero(self):
        self.assertNotEqual(run("doc", "note:doesnotexist")[0], 0)


# ---------------------------------------------------------------------------
# the rest of what the slash commands and the daily job call
# ---------------------------------------------------------------------------

class TestCore(CliTestCase):
    def test_prints_the_protocol_when_present(self):
        (self.core / "core.md").write_text("# Core Protocol\n\nAnswer first.\n",
                                           encoding="utf-8")
        code, out = run("core")
        self.assertEqual(code, 0)
        self.assertIn("Answer first", out)

    def test_a_missing_protocol_is_not_a_crash(self):
        """/gg runs this on every turn; it must degrade, not fail."""
        code, _out = run("core")
        self.assertIn(code, (0, 1))


class TestReindex(CliTestCase):
    def test_rebuild_recovers_the_same_corpus(self):
        """`reindex` deletes the index outright. Nothing verified the rebuild."""
        before = {d["doc_id"] for d in self.store().iter_documents()}
        self.assertEqual(run("reindex")[0], 0)
        run("ingest")
        self.assertEqual({d["doc_id"] for d in self.store().iter_documents()}, before)

    def test_the_ledger_survives_a_reindex(self):
        from gigabite.features import ledger as L
        led = L.Ledger.open()
        rid = led.start_run("outlive a reindex").run_id
        led.close()
        run("reindex")
        led = L.Ledger.open()
        self.addCleanup(led.close)
        self.assertIsNotNone(led.get_run(rid))


class TestSaveAndProject(CliTestCase):
    def test_save_writes_into_the_project_folder(self):
        code, _out = run("save", "--project", "acme", "--title", "Vendor call",
                         "They quoted 40k for the year.")
        self.assertEqual(code, 0)
        written = list((self.root / "acme").glob("*vendor-call*.md"))
        self.assertEqual(len(written), 1)
        self.assertIn("40k", written[0].read_text())

    def test_a_saved_note_is_findable_after_ingest(self):
        run("save", "--project", "acme", "--title", "Vendor call",
            "They quoted 40k for the year.")
        run("ingest")
        self.assertIn("Vendor call", run("search", "quoted", "vendor")[1])

    def test_project_list_names_the_projects(self):
        code, out = run("project", "list")
        self.assertEqual(code, 0)
        self.assertIn("acme", out)

    def test_creating_a_project_makes_a_folder(self):
        self.assertEqual(run("project", "add", "vendor-x", "--keywords", "vendorx")[0], 0)
        self.assertTrue((self.root / "vendor-x").is_dir())


class TestDecayAndSynthesis(CliTestCase):
    def test_decay_status_reports_without_archiving(self):
        active_before = len([d for d in self.store().iter_documents()])
        code, _out = run("decay", "--status")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.store().iter_documents()), active_before)

    def test_decay_defaults_to_a_dry_run(self):
        code, out = run("decay")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_synthesize_print_writes_no_proposal(self):
        code, _out = run("synthesize", "--print")
        self.assertEqual(code, 0)
        self.assertFalse(list(config.PROPOSALS_DIR.glob("*.md")))


class TestCalendar(CliTestCase):
    MEETINGS = [{"title": "Widget steering", "date": "2099-03-01",
                 "time": "10:00", "attendees": ["Dana"]}]

    def test_add_from_stdin_then_agenda(self):
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(self.MEETINGS))
        try:
            code, _out = run("calendar", "add", "--stdin")
        finally:
            sys.stdin = stdin
        self.assertEqual(code, 0)
        code, out = run("calendar", "agenda", "--day", "all")
        self.assertEqual(code, 0)
        self.assertIn("Widget steering", out)


class TestPaths(CliTestCase):
    def test_names_the_ledger_and_the_knowledge_root(self):
        code, out = run("paths")
        self.assertEqual(code, 0)
        self.assertIn(str(self.root), out)
        self.assertIn("ledger", out)


class TestNoArguments(CliTestCase):
    def test_bare_invocation_prints_help(self):
        code, out = run()
        self.assertEqual(code, 0)
        self.assertIn("usage", out.lower())


if __name__ == "__main__":
    unittest.main()
