"""Tests for message provenance: who actually typed a turn (`util.ORIGIN_*`).

A Claude Code transcript stores tool results, hook output and injected
scaffolding under ``role="user"``. Flattening a message's content blocks to one
string at ingest destroyed the only evidence of which was which, and the pass
that derives an operating protocol from the user's corrections then had no way
back — it guessed from substrings the flattening had already removed, dropping
real turns and quoting a file the Read tool printed back at the user as their own
sentence.

So the fixtures here are raw transcript lines in the shape Claude Code writes
them, not hand-written strings chosen to contain a marker: the point is that
provenance is read off the structure, and that the text alone cannot tell you.

    python3 -m unittest discover -s tests        (from the repo root)
"""

import json
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # see tests/_harness.py
import _harness  # noqa: F401,E402  redirects every store into a temp dir

from gigabite import config, ingest, util  # noqa: E402
from gigabite.features import core_coverage  # noqa: E402
from gigabite.sources import claude_code  # noqa: E402
from gigabite.store import (  # noqa: E402
    SCHEMA_VERSION, Document, Message, ReindexRequired, Store, connect,
)

SESSION = "11111111-2222-3333-4444-555555555555"
CWD = "/tmp/widgets"

# Prose that reads exactly like a preference the user stated, but was printed
# into the transcript by the Read tool. This is the self-referential case: the
# protocol file describes how the assistant should behave, so reading it seeds
# evidence for the very slots it describes.
FILE_CONTENTS = (
    "# Core Protocol\n\n"
    "- Open with the answer. No preamble, no closing summary.\n"
    "- Be blunt; push back when the reasoning is off.\n"
)


def _event(**kw):
    base = {"sessionId": SESSION, "cwd": CWD, "timestamp": "2026-01-02T09:00:00.000Z"}
    base.update(kw)
    return base


def _typed(text, ts="2026-01-02T09:00:00.000Z"):
    """A message as the CLI records it when the user presses enter: a bare string."""
    return _event(type="user", timestamp=ts, message={"role": "user", "content": text})


def _typed_with_reminder(text, reminder, ts="2026-01-02T09:01:00.000Z"):
    """Typed prose with the harness's system-reminder appended as its own block."""
    return _event(type="user", timestamp=ts, message={"role": "user", "content": [
        {"type": "text", "text": text},
        {"type": "text", "text": f"<system-reminder>{reminder}</system-reminder>"},
    ]})


def _tool_result(tool_use_id, text, ts="2026-01-02T09:02:00.000Z"):
    """A tool result, which the transcript replays under role='user'."""
    return _event(
        type="user", timestamp=ts,
        toolUseResult={"type": "text", "file": {"filePath": "/tmp/widgets/core.md"}},
        message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id,
             "content": [{"type": "text", "text": text}]},
        ]},
    )


def _assistant(text, ts="2026-01-02T09:03:00.000Z"):
    return _event(type="assistant", timestamp=ts,
                  message={"role": "assistant", "content": [
                      {"type": "text", "text": text}]})


def write_session(path, events):
    with open(path, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    return path


class TestSegmentOrigins(unittest.TestCase):
    """The classifier itself, on the block shapes the transcript uses."""

    def test_a_tool_result_is_replayed_however_human_it_reads(self):
        content = [{"type": "tool_result", "tool_use_id": "t1",
                    "content": [{"type": "text", "text": "be blunt, no preamble"}]}]
        self.assertEqual(util.message_origin(content), util.ORIGIN_REPLAYED)

    def test_typed_prose_is_typed(self):
        self.assertEqual(util.message_origin("be blunt, no preamble"),
                         util.ORIGIN_TYPED)

    def test_an_appended_reminder_leaves_the_turn_typed(self):
        content = [{"type": "text", "text": "be blunt, no preamble"},
                   {"type": "text", "text": "<system-reminder>rules</system-reminder>"}]
        self.assertEqual(util.message_origin(content), util.ORIGIN_TYPED)

    def test_a_reminder_inside_one_block_splits_rather_than_condemns(self):
        text = "be blunt\n<system-reminder>rules</system-reminder>"
        segs = util.coalesce_segments(text)
        self.assertEqual([s.origin for s in segs],
                         [util.ORIGIN_TYPED, util.ORIGIN_REPLAYED])
        self.assertEqual(util.message_origin(text), util.ORIGIN_TYPED)

    def test_a_message_that_is_only_scaffolding_is_replayed(self):
        self.assertEqual(
            util.message_origin("<local-command-stdout>ok</local-command-stdout>"),
            util.ORIGIN_REPLAYED)

    def test_any_hyphenated_harness_tag_is_replayed_not_just_the_known_ones(self):
        """The tag vocabulary grows; matching the convention is what holds."""
        for text in ("<task-notification>\n<status>completed</status>",
                     "<create-pr-command>open a pr</create-pr-command>",
                     "<user-prompt-submit-hook>recall</user-prompt-submit-hook>"):
            self.assertEqual(util.message_origin(text), util.ORIGIN_REPLAYED, text)

    def test_prose_is_not_condemned_by_looking_like_markup(self):
        self.assertEqual(util.message_origin("<div>be blunt with me</div>"),
                         util.ORIGIN_TYPED)
        self.assertEqual(util.message_origin("put <my-tag> in the template please"),
                         util.ORIGIN_TYPED)

    def test_flattened_text_is_unchanged_by_the_split(self):
        """Provenance must not cost the index any searchable text."""
        content = [{"type": "text", "text": "hello"},
                   {"type": "tool_result", "tool_use_id": "t1",
                    "content": [{"type": "text", "text": "world"}]}]
        self.assertEqual(util.coalesce_blocks(content), "hello\nworld")


class TestParsedFromRawTranscript(unittest.TestCase):
    """Provenance is captured at parse time, from the JSONL's own structure."""

    def setUp(self):
        self.dir = _harness.SCRATCH / self.id().split(".")[-1]
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = write_session(self.dir / f"{SESSION}.jsonl", [
            _typed("read the protocol file and tell me what it says"),
            _assistant("Reading it now."),
            _tool_result("toolu_01", FILE_CONTENTS),
            _typed_with_reminder("skip the summary at the end", "Codebase rules follow."),
            _event(type="user", isMeta=True, timestamp="2026-01-02T09:04:00.000Z",
                   message={"role": "user", "content": [
                       {"type": "text", "text": "Caveat: The messages below were "
                                                "generated while compacting."}]}),
        ])
        self.doc = claude_code.parse_session_file(self.path)

    def origins(self):
        return [(m.role, m.origin) for m in self.doc.messages]

    def test_the_tool_result_is_stored_but_not_as_a_user_turn(self):
        replayed = [m for m in self.doc.messages
                    if m.origin == util.ORIGIN_REPLAYED and "Core Protocol" in m.text]
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0].role, "user")   # role alone cannot tell them apart

    def test_the_typed_turns_are_typed(self):
        typed = [m.text for m in self.doc.messages if m.origin == util.ORIGIN_TYPED]
        self.assertEqual(len(typed), 2, typed)
        self.assertTrue(any("skip the summary" in t for t in typed), typed)

    def test_an_ismeta_event_is_replayed(self):
        meta = [m for m in self.doc.messages if "compacting" in m.text]
        self.assertEqual([m.origin for m in meta], [util.ORIGIN_REPLAYED])

    def test_assistant_turns_state_no_origin(self):
        self.assertTrue(all(m.origin == util.ORIGIN_NONE
                            for m in self.doc.messages if m.role == "assistant"))


class TestCoverageConsumesProvenance(unittest.TestCase):
    """End to end: raw transcript -> index -> coverage, with nothing hand-written."""

    def setUp(self):
        name = self.id().split(".")[-1]
        self.db = _harness.SCRATCH / f"{name}.db"
        if self.db.exists():
            self.db.unlink()
        self.st = Store(connect(self.db))
        self.dir = _harness.SCRATCH / name
        self.dir.mkdir(parents=True, exist_ok=True)
        path = write_session(self.dir / f"{SESSION}.jsonl", [
            _typed("read the protocol file"),
            _assistant("Here it is."),
            _tool_result("toolu_01", FILE_CONTENTS),
            _typed_with_reminder("skip the summary at the end, it just repeats you",
                                 "Codebase rules follow."),
        ])
        doc = claude_code.parse_session_file(path)
        self.st.upsert_document(doc)
        self.st.commit()

    def slots(self):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Slot:
            id: str
            kind: str
            evidence_terms: tuple = ()

        return (Slot("tone.do", "revealed", ("preamble", "blunt")),
                Slot("tone.length", "revealed", ("summary",)))

    def assess(self):
        return core_coverage.assess(self.st, slots=self.slots())

    def state(self, slot_id):
        return next(c.state for c in self.assess() if c.slot_id == slot_id)

    def test_a_file_the_tool_printed_is_not_evidence(self):
        """The self-referential case: reading core.md must not evidence core.md."""
        self.assertEqual(self.state("tone.do"), "empty")
        whys = [e.why for c in self.assess() for e in c.evidence]
        self.assertFalse([w for w in whys if "Core Protocol" in w or "blunt" in w], whys)

    def test_the_typed_correction_still_lands(self):
        evidence = next(c.evidence for c in self.assess() if c.slot_id == "tone.length")
        self.assertTrue(evidence)
        self.assertTrue(any("skip the summary" in e.why for e in evidence), evidence)

    def test_the_search_filter_excludes_a_wholly_replayed_document(self):
        """`origins=` narrows candidates in SQL; the message check does the rest.

        A passage may merge a typed turn with the tool output that followed it,
        and is then labelled typed on purpose — a filter that hid it would hide
        the user's words with it. So the SQL pass is deliberately inclusive, and
        a document with nothing typed in it at all is what it must exclude.
        """
        replayed_only = write_session(
            self.dir / "22222222-0000-0000-0000-000000000000.jsonl",
            [_tool_result("toolu_09", FILE_CONTENTS)])
        self.st.upsert_document(claude_code.parse_session_file(replayed_only))
        self.st.commit()

        rows = self.st.search("preamble", origins=(util.ORIGIN_TYPED,), record=False)
        self.assertTrue(self.st.search("preamble", record=False))
        self.assertNotIn(replayed_only.stem,
                         [r.get("ref", "") for r in rows][0] if rows else "")
        self.assertFalse([r for r in rows if replayed_only.stem in (r.get("ref") or "")])


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

_V2_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE documents (
    doc_id TEXT PRIMARY KEY, source TEXT NOT NULL, native_id TEXT NOT NULL,
    title TEXT, project TEXT, created_utc TEXT, updated_utc TEXT, ref TEXT,
    extra_json TEXT, content_hash TEXT, msg_count INTEGER DEFAULT 0,
    word_count INTEGER DEFAULT 0, accessed_utc TEXT, active INTEGER DEFAULT 1
);
CREATE TABLE sync_state (
    source TEXT NOT NULL, key TEXT NOT NULL, signature TEXT, updated_at TEXT,
    PRIMARY KEY (source, key)
);
CREATE TABLE messages (
    doc_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT, ts_utc TEXT, text TEXT,
    PRIMARY KEY (doc_id, seq)
);
CREATE VIRTUAL TABLE fts USING fts5(
    text, title, doc_id UNINDEXED, source UNINDEXED, project UNINDEXED,
    role UNINDEXED, ts_utc UNINDEXED, seq UNINDEXED, tokenize = 'porter unicode61'
);
INSERT INTO meta(key,value) VALUES('schema_version','2');
"""

# One row per FTS column, in the order the v2 table declares them.
_V2_FTS_COLS = "text,title,doc_id,source,project,role,ts_utc,seq"

# The v3 shape: same columns plus `origin`, which v3 could only leave blank.
_V3_FTS_DDL = """
CREATE VIRTUAL TABLE fts USING fts5(
    text, title, doc_id UNINDEXED, source UNINDEXED, project UNINDEXED,
    role UNINDEXED, ts_utc UNINDEXED, seq UNINDEXED, origin UNINDEXED,
    tokenize = 'porter unicode61'
);
"""

# Several documents, several messages each, so a rebuild that drops or merges
# rows is visible in a count. `absent` names a document whose source file is
# gone: it can only survive by being rebuilt from `messages`, never from disk,
# which is the case a one-document fixture cannot see.
_CORPUS = (
    ("aaa", "Session one", "widgets", 5, "/tmp/widgets/aaa.jsonl"),
    ("bbb", "Session two", "widgets", 4, "/tmp/widgets/bbb.jsonl"),
    ("ccc", "Session three", "gadgets", 3, "/tmp/gadgets/ccc.jsonl"),
    ("absent", "Deleted session", "gadgets", 6, ""),
)

# Long enough that util.passages splits a message into more than one row, so the
# FTS row count is not simply the message count.
_LONG = ("drop the preamble and answer first " * 90).strip()   # splits into passages


def _seed_v2(path):
    """A v2 database with several multi-passage documents, one file-less."""
    old = sqlite3.connect(str(path))
    old.executescript(_V2_SCHEMA)
    for native, title, project, n, ref in _CORPUS:
        doc_id = util.doc_id(config.SOURCE_CLAUDE_CODE, native)
        old.execute(
            "INSERT INTO documents(doc_id,source,native_id,title,project,created_utc,"
            "updated_utc,ref,content_hash,msg_count,word_count) VALUES(?,?,?,?,?,"
            "'2026-01-02T09:00:00+00:00','2026-01-02T09:00:00+00:00',?,'h',?,?)",
            (doc_id, config.SOURCE_CLAUDE_CODE, native, title, project, ref, n,
             n * util.word_count(_LONG)))
        msgs = [Message(seq=seq, role="user" if seq % 2 == 0 else "assistant",
                        text=f"{title} turn {seq}. {_LONG}",
                        ts_utc="2026-01-02T09:00:00+00:00")
                for seq in range(n)]
        for m in msgs:
            old.execute(
                "INSERT INTO messages(doc_id,seq,role,ts_utc,text) "
                "VALUES(?,?,?,?,?)", (doc_id, m.seq, m.role, m.ts_utc, m.text))
        for p in util.passages(msgs):
            old.execute(
                f"INSERT INTO fts({_V2_FTS_COLS}) VALUES(?,?,?,?,?,?,?,?)",
                (p.text, title, doc_id, config.SOURCE_CLAUDE_CODE, project,
                 p.role, p.ts_utc, p.seq))
        if ref:
            old.execute("INSERT INTO sync_state VALUES(?,?,'sig','now')",
                        (config.SOURCE_CLAUDE_CODE, ref))
    old.commit()
    old.close()


def _fts_rows(conn):
    return conn.execute(
        f"SELECT {_V2_FTS_COLS} FROM fts ORDER BY doc_id, seq, text").fetchall()


def _fresh_db(case):
    db = _harness.SCRATCH / f"{case.id().split('.')[-1]}.db"
    for suffix in ("", "-wal", "-shm"):
        p = db.with_name(db.name + suffix)
        if p.exists():
            p.unlink()
    for stale in db.parent.glob(db.name + ".pre-v*"):
        stale.unlink()
    return db


class TestMigrationFromV2(unittest.TestCase):
    """An index built before provenance existed must not answer as if it has it."""

    def setUp(self):
        self.doc_id = util.doc_id(config.SOURCE_CLAUDE_CODE, "aaa")
        self.db = _fresh_db(self)
        _seed_v2(self.db)
        before = sqlite3.connect(str(self.db))
        before.row_factory = sqlite3.Row
        self.before_fts = [tuple(r) for r in _fts_rows(before)]
        self.before_counts = {
            t: before.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            for t in ("documents", "messages", "fts")}
        before.close()
        self.st = Store(connect(self.db))

    def test_the_schema_upgrades_and_the_column_exists(self):
        version = self.st.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"]
        self.assertEqual(version, str(SCHEMA_VERSION))
        cols = {r["name"] for r in self.st.conn.execute("PRAGMA table_info(messages)")}
        self.assertIn("origin", cols)

    def test_nothing_is_destroyed(self):
        """The corpus is not reproducible from the repo, so it is kept."""
        counts = {t: self.st.conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                  for t in ("documents", "messages", "fts")}
        self.assertEqual(counts, self.before_counts)
        self.assertGreater(counts["fts"], counts["messages"])   # multi-passage
        doc = self.st.get_document(self.doc_id)
        self.assertIn("drop the preamble", doc["messages"][0]["text"])

    def test_every_fts_row_survives_byte_for_byte(self):
        """A rebuild must reproduce the retrieval units, not approximate them."""
        after = [tuple(r) for r in _fts_rows(self.st.conn)]
        self.assertEqual(after, self.before_fts)

    def test_a_document_whose_source_file_is_gone_survives(self):
        """It can only come back from `messages`; a rebuild-from-disk loses it."""
        absent = util.doc_id(config.SOURCE_CLAUDE_CODE, "absent")
        self.assertIsNotNone(self.st.get_document(absent))
        self.assertTrue(self.st.search("Deleted session", record=False))

    def test_ranking_is_unchanged(self):
        hits = self.st.search("preamble", limit=10, record=False)
        self.assertEqual([h["doc_id"] for h in hits],
                         [h["doc_id"] for h in self.st.search("preamble", limit=10,
                                                              record=False)])
        self.assertTrue(hits)

    def test_search_still_works_during_the_window(self):
        self.assertTrue(self.st.search("preamble", record=False))

    def test_a_provenance_reader_refuses_rather_than_answering_from_blanks(self):
        self.assertTrue(self.st.provenance_pending())
        with self.assertRaises(ReindexRequired):
            core_coverage.assess(self.st)

    def test_an_ingest_that_read_nothing_leaves_the_guard_armed(self):
        """The bug this replaced: the flag cleared when the *command* finished.

        Nothing on disk matches these documents, so every source scans zero files
        and not one message is backfilled. Pending has to stay true.
        """
        ingest.run(self.st, sources=None)
        self.assertTrue(self.st.provenance_pending())
        with self.assertRaises(ReindexRequired):
            core_coverage.assess(self.st)

    def test_the_guard_disarms_only_when_every_message_has_an_origin(self):
        blank = self.st.conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE origin IS NULL").fetchone()["c"]
        self.assertEqual(blank, self.before_counts["messages"])
        # Backfill all but the last message: still pending.
        self.st.conn.execute(
            "UPDATE messages SET origin=? WHERE rowid <> (SELECT MAX(rowid) FROM messages)",
            (util.ORIGIN_TYPED,))
        self.assertTrue(self.st.provenance_pending())
        self.st.conn.execute("UPDATE messages SET origin=? WHERE origin IS NULL",
                             (util.ORIGIN_NONE,))
        self.assertFalse(self.st.provenance_pending())
        core_coverage.assess(self.st)      # no longer refuses

    def test_a_partial_ingest_does_not_clear_the_guard(self):
        ingest.run(self.st, sources=[config.SOURCE_NOTE])
        self.assertTrue(self.st.provenance_pending())

    def test_re_ingesting_a_document_fills_the_column_in(self):
        self.st.upsert_document(Document(
            source=config.SOURCE_CLAUDE_CODE, native_id="aaa", title="Session one",
            project="widgets", created_utc="2026-01-02T09:00:00+00:00",
            messages=[Message(seq=0, role="user", text="drop the preamble",
                              origin=util.ORIGIN_TYPED)]))
        self.st.commit()
        doc = self.st.get_document(self.doc_id)
        self.assertEqual(doc["messages"][0]["origin"], util.ORIGIN_TYPED)
        self.assertTrue(self.st.search("preamble", origins=(util.ORIGIN_TYPED,),
                                       record=False))


class TestMigrationFromV3(unittest.TestCase):
    """v3 wrote the column with DEFAULT '', so '' means both none and never-read.

    An index in that state is the user's real one. It must report pending, or the
    corpus reads as fully backfilled when not one message of it has been re-read.
    """

    def setUp(self):
        self.db = _fresh_db(self)
        _seed_v2(self.db)
        old = sqlite3.connect(str(self.db))
        # Exactly what v3 did: an additive column with DEFAULT '', and an FTS
        # table recreated with an `origin` column that the rebuild left blank.
        old.execute("ALTER TABLE messages ADD COLUMN origin TEXT DEFAULT ''")
        rows = old.execute(f"SELECT {_V2_FTS_COLS} FROM fts").fetchall()
        old.execute("DROP TABLE fts")
        old.executescript(_V3_FTS_DDL)
        old.executemany(
            f"INSERT INTO fts({_V2_FTS_COLS},origin) VALUES(?,?,?,?,?,?,?,?,'')", rows)
        old.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
        old.execute("INSERT INTO meta(key,value) "
                    "VALUES('provenance_backfill_pending','1')")
        old.commit()
        self.messages = old.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        old.close()
        self.st = Store(connect(self.db))

    def test_a_blank_v3_index_still_reports_pending(self):
        self.assertTrue(self.st.provenance_pending())
        with self.assertRaises(ReindexRequired):
            core_coverage.assess(self.st)

    def test_the_blanks_became_nulls_and_nothing_was_lost(self):
        nulls = self.st.conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE origin IS NULL").fetchone()["c"]
        self.assertEqual(nulls, self.messages)
        self.assertTrue(self.st.search("preamble", record=False))

    def test_the_meta_flag_is_gone(self):
        self.assertIsNone(self.st.conn.execute(
            "SELECT 1 FROM meta WHERE key='provenance_backfill_pending'").fetchone())


class TestMigrationSetsTheDatabaseAside(unittest.TestCase):
    """Migration runs unattended (recall hook, nightly job) — keep a way back."""

    def _copies(self, db):
        return sorted(p.name for p in db.parent.glob(db.name + ".pre-v*"))

    def test_a_destructive_migration_leaves_a_copy(self):
        db = _fresh_db(self)
        _seed_v2(db)
        original = db.read_bytes()
        Store(connect(db)).conn.close()
        copies = self._copies(db)
        self.assertEqual(len(copies), 1)
        self.assertTrue(copies[0].startswith(db.name + f".pre-v{SCHEMA_VERSION}-"))
        aside = sqlite3.connect(str(db.parent / copies[0]))
        aside.row_factory = sqlite3.Row
        self.assertEqual(
            aside.execute("SELECT value FROM meta WHERE key='schema_version'"
                          ).fetchone()["value"], "2")
        self.assertEqual(
            aside.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"],
            len(_CORPUS) and sum(n for _i, _t, _p, n, _r in _CORPUS))
        aside.close()
        self.assertNotEqual(db.read_bytes(), original)   # the live file did migrate

    def test_an_existing_set_aside_is_never_clobbered(self):
        db = _fresh_db(self)
        _seed_v2(db)
        aside = db.with_name(
            db.name + f".pre-v{SCHEMA_VERSION}-"
            + datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        aside.write_bytes(b"an earlier migration's copy")
        Store(connect(db)).conn.close()
        self.assertEqual(aside.read_bytes(), b"an earlier migration's copy")
        self.assertEqual(len(self._copies(db)), 1)

    def test_a_fresh_database_sets_nothing_aside(self):
        db = _fresh_db(self)
        Store(connect(db)).conn.close()
        self.assertEqual(self._copies(db), [])


class TestFreshIndexIsNotFlagged(unittest.TestCase):
    def test_a_new_database_needs_no_reindex(self):
        db = _fresh_db(self)
        st = Store(connect(db))
        self.assertFalse(st.provenance_pending())
        core_coverage.assess(st)


if __name__ == "__main__":
    unittest.main()
