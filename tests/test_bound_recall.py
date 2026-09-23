"""Ambient recall for a brand-new user: a bound folder recalls its own sessions.

The fresh-install loop failed in three places, each silent:

  A1. A folder bound to a project whose name differs from the folder's recalled
      none of its own Claude Code sessions — ingest tagged them by basename, and
      recall searches only the bound project.
  A2. The hook's absolute bm25 cut-off (`score < -1.0`) injected nothing on a
      small index, where idf collapses and an exact match scores ~ -5e-06.
  B1. The "which project?" ask was spent when emitted, so a missed ask left the
      folder silent for a month.

Plus two small ones pinned here: `save --project` reports a project it creates
(C3), and search snippets do not highlight stop words (C9).

Every subprocess gets a private HOME; nothing here reads the real one.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
from _harness import TempRoot  # noqa: E402  (must precede any gigabite import)

from gigabite import cli, config, util  # noqa: E402
from gigabite.features import bindings, routing, save  # noqa: E402
from gigabite.sources import claude_code  # noqa: E402
from gigabite.store import Document, Message, Store, connect  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SOURCE = REPO_ROOT / "install" / "hooks" / "gg-recall.sh"

SESSIONS = {
    "s-cache": ("Catalogue caching strategy",
                "how should we cache the product catalogue for widgetshop?",
                "We decided to cache the catalogue at the edge for ten minutes and "
                "purge it on every price import."),
    "s-checkout": ("Checkout button colour",
                   "the checkout button needs a clearer colour",
                   "Switched the checkout button to the high-contrast green."),
}


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


def write_session(projects_dir: Path, native: str, cwd: str, title: str,
                  user: str, assistant: str) -> Path:
    folder = projects_dir / "-code-widgetshop"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{native}.jsonl"
    events = [
        {"type": "user", "sessionId": native, "cwd": cwd,
         "timestamp": "2026-09-01T10:00:00Z",
         "message": {"role": "user", "content": user}},
        {"type": "assistant", "sessionId": native, "cwd": cwd,
         "timestamp": "2026-09-01T10:00:05Z",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": assistant}]}},
        {"type": "ai-title", "aiTitle": title},
    ]
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


class SessionCase(TempRoot):
    """Two synthetic sessions run in a checkout called `widgetshop`."""

    def setUp(self):
        super().setUp()
        base = self.root.parent
        self.home = base / "home"
        self.home.mkdir()
        self.work = base / "code" / "widgetshop"
        (self.work / ".git").mkdir(parents=True)
        self.work_real = os.path.realpath(str(self.work))
        for native, (title, user, assistant) in SESSIONS.items():
            write_session(config.CLAUDE_CODE_PROJECTS_DIR, native, str(self.work),
                          title, user, assistant)
        self.st = Store(connect(config.DB_PATH))
        claude_code.ingest(self.st)

    def tags(self):
        """{doc_id: (documents.project, {fts.project, ...})} for Claude Code docs."""
        out = {}
        for row in self.st.conn.execute(
                "SELECT doc_id, project FROM documents WHERE source=?",
                (config.SOURCE_CLAUDE_CODE,)):
            fts = {r[0] for r in self.st.conn.execute(
                "SELECT DISTINCT project FROM fts WHERE doc_id=?", (row[0],))}
            out[row[0]] = (row[1], fts)
        return out

    def tag_of(self, native):
        return self.tags()[util.doc_id(config.SOURCE_CLAUDE_CODE, native)][0]

    def route(self, prompt, cwd=None, session_id=None):
        return routing.route(self.st, prompt, cwd=str(cwd or self.work),
                             session_id=session_id)


# ---------------------------------------------------------------------------
# A1 — a bound folder recalls its own sessions, whatever the project is called
# ---------------------------------------------------------------------------

class TestABoundFolderRecallsItsOwnSessions(SessionCase):

    def test_before_binding_they_are_filed_by_folder_name(self):
        self.assertEqual({p for p, _ in self.tags().values()}, {"widgetshop"})

    def test_binding_to_a_differently_named_project_brings_the_sessions_with_it(self):
        code, out = run("project", "bind", "acme", "--dir", str(self.work))
        self.assertEqual(code, 0, out)
        self.assertIn("created project acme", out)
        self.assertIn("re-filed 2", out)
        for project, fts in self.tags().values():
            self.assertEqual(project, "acme")
            self.assertEqual(fts, {"acme"}, "documents and fts must agree")
        result = self.route("what did we decide about caching the catalogue?")
        self.assertEqual(result["context"]["project"], "acme")
        titles = [h["title"] for h in result["hits"] if h["inject"]]
        self.assertIn("Catalogue caching strategy", titles)

    def test_a_session_ingested_after_binding_is_filed_under_the_binding(self):
        run("project", "bind", "acme", "--dir", str(self.work))
        write_session(config.CLAUDE_CODE_PROJECTS_DIR, "s-later", str(self.work),
                      "Search relevance", "tune the search relevance", "Done.")
        claude_code.ingest(self.st)
        self.assertEqual(self.tag_of("s-later"), "acme")

    def test_a_subfolder_session_follows_the_workspace_binding(self):
        sub = self.work / "src" / "api"
        sub.mkdir(parents=True)
        write_session(config.CLAUDE_CODE_PROJECTS_DIR, "s-sub", str(sub),
                      "API pagination", "paginate the api", "Done.")
        claude_code.ingest(self.st)
        run("project", "bind", "acme", "--dir", str(self.work))
        self.assertEqual(self.tag_of("s-sub"), "acme")

    def test_not_project_work_files_the_sessions_under_no_project(self):
        run("project", "bind", "--none", "--dir", str(self.work))
        self.assertEqual({p for p, _ in self.tags().values()}, {""})

    def test_forgetting_the_binding_puts_them_back(self):
        run("project", "bind", "acme", "--dir", str(self.work))
        run("project", "bind", "--forget", "--dir", str(self.work))
        self.assertEqual({p for p, _ in self.tags().values()}, {"widgetshop"})

    def test_a_checkout_elsewhere_is_left_alone(self):
        other = self.root.parent / "code2" / "gadgets"
        (other / ".git").mkdir(parents=True)
        write_session(config.CLAUDE_CODE_PROJECTS_DIR, "s-other", str(other),
                      "Gadget backlog", "gadget backlog", "Done.")
        claude_code.ingest(self.st)
        run("project", "bind", "acme", "--dir", str(self.work))
        self.assertEqual(self.tag_of("s-other"), "gadgets")

    def test_reindex_gives_the_same_answer_as_the_retag(self):
        run("project", "bind", "acme", "--dir", str(self.work))
        before = self.tags()
        self.st.conn.close()
        run("reindex")
        self.st = Store(connect(config.DB_PATH))
        self.assertEqual({k: v[0] for k, v in self.tags().items()},
                         {k: v[0] for k, v in before.items()})


# ---------------------------------------------------------------------------
# A2 — the recall gate works on a two-session index and stays quiet on noise
# ---------------------------------------------------------------------------

@unittest.skipUnless(Path("/bin/bash").exists() and Path("/usr/bin/python3").exists(),
                     "the hook is bash + /usr/bin/python3 by construction")
class HookCase(SessionCase):

    def setUp(self):
        super().setUp()
        self.bin_dir = Path(tempfile.mkdtemp(prefix="gigabite-hook-"))
        self.addCleanup(shutil.rmtree, str(self.bin_dir), True)
        shim = self.bin_dir / "gigabite"
        shim.write_text('#!/bin/sh\nexec /usr/bin/python3 -m gigabite "$@"\n')
        shim.chmod(0o755)
        self.hook = self.bin_dir / "gg-recall.sh"
        self.hook.write_text(HOOK_SOURCE.read_text(encoding="utf-8")
                             .replace("__GIGABITE_BIN__", str(shim)), encoding="utf-8")
        self.hook.chmod(0o755)

    def fire(self, prompt, session_id=None, cwd=None):
        payload = {"prompt": prompt, "hook_event_name": "UserPromptSubmit",
                   "cwd": str(cwd or self.work)}
        if session_id:
            payload["session_id"] = session_id
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), HOME=str(self.home))
        proc = subprocess.run(["/bin/bash", str(self.hook)], input=json.dumps(payload),
                              env=env, cwd=str(cwd or self.work),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout


class TestTheGateWorksOnASmallIndex(HookCase):

    def test_a_two_session_index_injects_the_matching_session(self):
        run("project", "bind", "acme", "--dir", str(self.work))
        hits = self.route("what did we decide about caching the catalogue?")["hits"]
        self.assertTrue(hits)
        self.assertFalse([h for h in hits if h["score"] < -1.0],
                         "the old absolute cut-off would have injected nothing")
        out = self.fire("what did we decide about caching the catalogue?")
        self.assertIn("[gigabite recall", out)
        self.assertIn("Catalogue caching strategy", out)
        self.assertNotIn("Checkout button colour", out)

    def test_a_prompt_that_only_shares_stop_words_injects_nothing(self):
        run("project", "bind", "acme", "--dir", str(self.work))
        out = self.fire("what should we do about the weekend rota?")
        self.assertEqual(out, "")

    def test_a_prompt_starting_with_a_dash_is_still_routed(self):
        run("project", "bind", "acme", "--dir", str(self.work))
        out = self.fire("--catalogue caching: what did we decide?")
        self.assertIn("Catalogue caching strategy", out)


class TestTheGateStaysQuietOnALargeIndex(HookCase):
    """A thousand documents in the bound project. The irrelevant prompt shares one
    moderately common word with a tenth of them — enough for bm25 to score those
    well past the old cut-off — and none of its distinctive words."""

    def setUp(self):
        super().setUp()
        run("project", "bind", "acme", "--dir", str(self.work))
        for i in range(1000):
            common = "the deployment pipeline" if i % 10 == 0 else "the sprint board"
            self.st.upsert_document(Document(
                source=config.SOURCE_NOTE, native_id=f"filler-{i}",
                title=f"Standup {i}", project="acme",
                messages=[Message(0, "note",
                                  f"Routine notes on {common}, staffing and "
                                  f"ticket {i} for the team.")]))
        self.st.commit()

    PROMPT = "why is the deployment of the mobile push notifications flaky on android?"

    def test_the_old_cut_off_would_have_injected_noise(self):
        hits = self.route(self.PROMPT)["hits"]
        self.assertTrue([h for h in hits if h["score"] < -1.0])

    def test_an_irrelevant_prompt_injects_nothing(self):
        hits = self.route(self.PROMPT)["hits"]
        self.assertTrue(hits, "the gate, not an empty search, must be what stops it")
        self.assertEqual([h for h in hits if h["inject"]], [])
        self.assertEqual(self.fire(self.PROMPT), "")

    def test_the_relevant_session_is_still_injected(self):
        out = self.fire("what did we decide about caching the catalogue?")
        self.assertIn("Catalogue caching strategy", out)


class TestKeywordResolvedTurnsKeepARelativeFloor(SessionCase):
    """Without a binding or @marker, scope came from a word in the prompt, so a hit
    far weaker than the best one is not injected even if it covers the words."""

    def test_a_much_weaker_hit_is_not_injected(self):
        hits = [{"rowid": 1, "score": -10.0}, {"rowid": 2, "score": -2.0}]

        class Stub:
            def term_coverage(self, terms, rowids):
                return {r: 1.0 for r in rowids}

        routing.mark_injectable(Stub(), "catalogue caching", hits, "keyword")
        self.assertEqual([h["inject"] for h in hits], [True, False])
        routing.mark_injectable(Stub(), "catalogue caching", hits, "binding")
        self.assertEqual([h["inject"] for h in hits], [True, True])


# ---------------------------------------------------------------------------
# B1 — the ask is spent by the answer, and repeats once per session until then
# ---------------------------------------------------------------------------

class TestTheAskRepeatsPerSessionUntilAnswered(HookCase):

    PROMPT = "what did we decide about caching the catalogue?"

    def asks(self, session_id):
        return "gigabite project bind" in self.fire(self.PROMPT, session_id=session_id)

    def test_once_per_session_until_bound(self):
        self.assertEqual([self.asks("s1"), self.asks("s1"), self.asks("s1")],
                         [True, False, False])
        self.assertTrue(self.asks("s2"), "a missed ask comes back next session")
        self.assertFalse(self.asks("s2"))
        run("project", "bind", "acme", "--dir", str(self.work))
        self.assertFalse(self.asks("s3"), "answered: never asked again")
        self.assertIn("Catalogue caching strategy",
                      self.fire(self.PROMPT, session_id="s3"))

    def test_not_project_work_is_an_answer_too(self):
        self.assertTrue(self.asks("s1"))
        run("project", "bind", "--none", "--dir", str(self.work))
        self.assertFalse(self.asks("s2"))

    def test_the_ask_gives_the_one_step_answer(self):
        out = self.fire(self.PROMPT, session_id="s1")
        self.assertIn("gigabite project bind <name> --dir", out)
        self.assertNotIn("project add", out)

    def test_a_session_id_cannot_smuggle_anything_into_the_command(self):
        out = self.fire(self.PROMPT, session_id="s1; rm -rf /tmp/x $(id)")
        self.assertIn("gigabite project bind", out)
        record = json.loads(config.BINDINGS_FILE.read_text())["asked"]
        self.assertEqual(record[self.work_real]["session"], "s1rm-rftmpxid")

    def test_the_older_timestamp_record_still_reads(self):
        bindings._save({}, {self.work_real: "2026-01-01T00:00:00+00:00"})
        self.assertTrue(self.asks("s1"))


# ---------------------------------------------------------------------------
# C3 / C9
# ---------------------------------------------------------------------------

class TestSaveReportsAProjectItCreates(TempRoot):

    def test_a_new_project_is_reported(self):
        code, out = run("save", "a note about queues", "--project", "fresh")
        self.assertEqual(code, 0)
        self.assertIn("created project fresh", out)

    def test_an_existing_project_is_not(self):
        save.ensure_project("known")
        code, out = run("save", "a note about queues", "--project", "known")
        self.assertEqual(code, 0)
        self.assertNotIn("created project", out)


class TestSnippetsDoNotHighlightStopWords(TempRoot):

    def test_only_meaningful_words_are_marked(self):
        st = Store(connect(self.db))
        st.upsert_document(Document(
            source=config.SOURCE_NOTE, native_id="n1", title="Cache notes", project="p",
            messages=[Message(0, "note", "We said the catalogue cache is what we "
                                          "decide about at the weekly review.")]))
        st.commit()
        snips = " ".join(h["snippet"] for h in
                         st.search("what did we decide about caching the catalogue"))
        self.assertIn("«catalogue»", snips)
        for word in ("We", "we", "the", "what", "about"):
            self.assertNotIn("«%s»" % word, snips)


if __name__ == "__main__":
    unittest.main()
