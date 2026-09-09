"""The local search index: SQLite + FTS5.

One row per message in a standalone FTS5 table (content stored, so normal
INSERT/UPDATE/DELETE work). The document's title is denormalised onto every
message row so a title hit surfaces the document and can be weighted via bm25.

At this scale (thousands of meetings/chats, ~10^5 messages) a single local
SQLite file is the right tool — no server, no embeddings, nothing off-device.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config, util

SCHEMA_VERSION = 4

# Ranking penalty applied to the user's own Claude Code transcripts.
#
# A transcript of you asking about X is not evidence about X. It quotes your
# question verbatim — and any tool output that answered it, including every term
# that was searched — so it is a dense, near-perfect match for exactly the
# question you are about to ask again, and it buries the source material that
# actually answers it. Left alone this gets steadily worse, because the pile of
# transcripts only grows.
#
# bm25 scores are negative and more negative ranks higher, so multiplying by a
# factor below 1 moves a row toward zero: ranked lower, never excluded. A
# transcript still wins when it is the only real match (e.g. "what did we decide
# in that session"), which is why this is a penalty and not a filter.
#
# Treat this as a tie-breaker, not a fix. An earlier calibration note claimed
# 1/6 queries kept a transcript on top at 0.30; re-measuring later showed 5/6,
# because the corpus had grown and nobody had re-run it. Sweeping from 1.0 down
# to 0.15 barely moved the result — the lever was close to exhausted, which is
# what prompted fixing the row-shape problem in ``util.passages`` instead.
#
# With passages in place the value was swept again over 228 known-item queries
# (tools/eval_recall.py). The result argued for *less* penalty, not more:
#
#   penalty   top-1   recall@5   MRR     transcripts beating source material
#     1.00    89.9%     96.5%    0.927                 2.9%
#     0.70    91.7%     96.1%    0.936                 0.0%
#     0.30    91.7%     95.6%    0.935                 0.0%
#     0.10    91.7%     95.6%    0.934                 0.0%
#
# 0.70 is the knee: it removes every case of a transcript displacing source
# material, while suppressing transcripts less than 0.30 did and so retrieving
# them better when they genuinely are the answer. Below 0.70 nothing improves —
# the extra suppression only costs recall. Re-run the sweep before changing it.
TRANSCRIPT_RANK_PENALTY = 0.70

# How many passages any single document may contribute to one result page.
#
# Without a cap, one long document can occupy every slot: 'inbox drop folder'
# returned three hits that were all the same session, which is three views of one
# answer where the user wanted three answers. Two lets a genuinely rich source
# show its best pair of passages without crowding out the field.
MAX_HITS_PER_DOC = 2


class ReindexRequired(RuntimeError):
    """The index predates a schema change that cannot be backfilled in place.

    Raised by readers whose answer would otherwise be wrong-but-plausible on an
    un-upgraded index — message provenance is the case that forced it: the column
    exists after migration but is unpopulated until every document has been
    re-parsed from its source file, and a coverage pass run in between would
    report "your history says nothing" rather than an error.
    """


@dataclass
class Message:
    seq: int
    role: str
    text: str
    ts_utc: str = ""
    # Where the text came from: util.ORIGIN_TYPED / ORIGIN_REPLAYED / ORIGIN_NONE.
    # Sources that have no typed/replayed distinction to make (meetings, notes,
    # calendar entries — one role per document, nothing replayed into them) leave
    # this empty rather than assert an origin they cannot know.
    origin: str = util.ORIGIN_NONE


@dataclass
class Document:
    source: str
    native_id: str
    title: str = ""
    project: str = ""
    created_utc: str = ""
    updated_utc: str = ""
    ref: str = ""                       # file path, url, or other pointer back to source
    extra: dict = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)

    # An explicit doc_id, used when this file *is* an existing document rather
    # than a new one. A materialized conversation (features.materialize) is a
    # readable rendering of a chat that is already indexed under its own source,
    # so it must resolve to that same doc_id — otherwise the same conversation
    # would be indexed twice and every search would return it twice. The
    # rendering declares the id it belongs to in its frontmatter, and
    # sources.notes passes it through here. Empty means "derive it", which is
    # every ordinary document.
    doc_id_override: str = ""

    @property
    def doc_id(self) -> str:
        return self.doc_id_override or util.doc_id(self.source, self.native_id)

    def content_hash(self) -> str:
        h = hashlib.sha1()
        h.update(self.title.encode("utf-8"))
        for m in self.messages:
            h.update(b"\x00")
            h.update(f"{m.seq}|{m.role}|{m.ts_utc}|{m.origin}|".encode("utf-8"))
            h.update(m.text.encode("utf-8"))
        return h.hexdigest()


# ---------------------------------------------------------------------------
# connection + schema
# ---------------------------------------------------------------------------

def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    config.ensure_dirs()
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL lets readers run anytime; busy_timeout makes a concurrent WRITER wait
    # (up to 5s) for the lock instead of erroring — so using the tool while the
    # scheduled daily job runs is safe.
    conn.execute("PRAGMA busy_timeout=5000")
    init_schema(conn)
    return conn


# Held as a constant because an FTS5 table cannot be ALTERed: adding a column
# means dropping and recreating it, so the migration needs the same DDL the
# initial create uses, not a second copy of it that can drift.
_FTS_DDL = """
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
            text,
            title,
            doc_id  UNINDEXED,
            source  UNINDEXED,
            project UNINDEXED,
            role    UNINDEXED,
            ts_utc  UNINDEXED,
            seq     UNINDEXED,
            origin  UNINDEXED,
            tokenize = 'porter unicode61'
        );
"""

# An un-backfilled message is one whose `origin` is NULL, and that is the whole
# of the bookkeeping — see ``Store.provenance_pending``. There is deliberately no
# meta flag for it: a flag is a second copy of a fact the rows already state, and
# the two drifted apart the moment an ingest command finished without every
# source actually having re-read its files.


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            doc_id       TEXT PRIMARY KEY,
            source       TEXT NOT NULL,
            native_id    TEXT NOT NULL,
            title        TEXT,
            project      TEXT,
            created_utc  TEXT,
            updated_utc  TEXT,
            ref          TEXT,
            extra_json   TEXT,
            content_hash TEXT,
            msg_count    INTEGER DEFAULT 0,
            word_count   INTEGER DEFAULT 0,
            accessed_utc TEXT,
            active       INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_documents_source  ON documents(source);
        CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project);
        CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_utc);

        -- Incremental-ingest bookkeeping, keyed by source file / cursor.
        CREATE TABLE IF NOT EXISTS sync_state (
            source     TEXT NOT NULL,
            key        TEXT NOT NULL,
            signature  TEXT,
            updated_at TEXT,
            PRIMARY KEY (source, key)
        );

        -- The verbatim conversation. FTS holds passages (see util.passages),
        -- which merge and split messages for even retrieval units; this table
        -- keeps the real messages so `show`, decay and synthesis can reproduce a
        -- document exactly as it was written.
        CREATE TABLE IF NOT EXISTS messages (
            doc_id TEXT NOT NULL,
            seq    INTEGER NOT NULL,
            role   TEXT,
            ts_utc TEXT,
            text   TEXT,
            -- No DEFAULT, on purpose. NULL means "never written", which is what
            -- makes an un-backfilled row distinguishable from ORIGIN_NONE ('' —
            -- legitimately not applicable, as on an assistant turn). A default
            -- of '' collapses the two and the distinction cannot be recovered.
            origin TEXT,
            PRIMARY KEY (doc_id, seq)
        );
        """
        + _FTS_DDL
    )
    _migrate(conn)
    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


def _set_aside(conn: sqlite3.Connection) -> None:
    """Copy the database to `gigabite.db.pre-v<N>-<date>` before a destructive step.

    The corpus is not reproducible from the repo, and migration runs unattended —
    the ambient recall hook and the nightly job both open the index, so the first
    thing to touch it after an upgrade is usually not a person. Same convention as
    ``refresh_doc`` in install.sh: set aside, never destroy, and never overwrite an
    existing set-aside (a second migration must not eat the first one's copy).
    """
    src = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not src:                                   # :memory: — nothing to copy
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = Path(f"{src}.pre-v{SCHEMA_VERSION}-{stamp}")
    if dest.exists():
        return
    with sqlite3.connect(str(dest)) as out:       # backup(), so WAL content comes too
        conn.backup(out)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a pre-existing database up to the current schema."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    if "accessed_utc" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN accessed_utc TEXT")
    if "active" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN active INTEGER DEFAULT 1")

    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    version = int(row["value"]) if row and str(row["value"]).isdigit() else 1

    if row is None and not conn.execute(
            "SELECT 1 FROM documents LIMIT 1").fetchone():
        # A database this call has just created: the tables were made at the
        # current version, so there is nothing to migrate and nothing to flag.
        # Without this a fresh index would inherit the upgrade's pending marker.
        conn.commit()
        return

    if version < SCHEMA_VERSION:
        # Everything below rewrites data in place and cannot be undone, so the
        # pre-migration file is set aside first (finding: the index was upgraded
        # unattended, by a hook, with no copy to go back to).
        _set_aside(conn)

    if version < 2:
        # v1 stored one FTS row per message; v2 stores passages and keeps the
        # verbatim messages separately. The rows cannot be converted in place, so
        # clear the derived data and the ingest signatures — the next ingest
        # rebuilds everything from the files on disk, which remain the source of
        # truth. Documents rows are kept so access/decay history survives.
        conn.execute("DELETE FROM fts")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sync_state")
        conn.execute("UPDATE documents SET content_hash = NULL")

    if version < 3:
        # v3 records message provenance (util.ORIGIN_*). The information exists
        # only in the raw transcripts, so it cannot be recovered from what is
        # stored — every document has to be re-parsed from its source file.
        #
        # Nothing is thrown away to achieve that. `messages` takes an additive
        # column; `fts` has to be recreated because FTS5 cannot ALTER, so it is
        # rebuilt from the surviving messages and search keeps working meanwhile.
        # Clearing `sync_state` and `content_hash` makes the next ingest re-read
        # and rewrite every document, which is what fills the column in.
        #
        # Until that happens the column is NULL, which would make a
        # provenance-filtered query answer "nothing" instead of failing — so
        # readers that would be wrong in that window raise ReindexRequired. The
        # column is added without a DEFAULT so those rows stay distinguishable
        # from a genuine ORIGIN_NONE; see ``Store.provenance_pending``.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "origin" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN origin TEXT")
        conn.execute("DROP TABLE IF EXISTS fts")
        conn.executescript(_FTS_DDL)
        _rebuild_fts_from_messages(conn)
        conn.execute("DELETE FROM sync_state")
        conn.execute("UPDATE documents SET content_hash = NULL")

    if version < 4:
        # v3 added `origin` with DEFAULT '', so every pre-existing row read as
        # ORIGIN_NONE ("not applicable") the instant the column appeared, and a
        # separate meta flag carried the "not backfilled yet" fact instead. The
        # flag could be cleared while the rows were still blank; the rows could
        # not say so themselves. v4 removes the flag and makes NULL mean it.
        #
        # Any '' written under v3 is therefore untrustworthy — it may be a real
        # ORIGIN_NONE or an un-backfilled row, and nothing distinguishes them —
        # so it goes back to NULL and is re-read from the source file. The cost
        # is one re-ingest; the alternative is a corpus that reports itself
        # backfilled when none of it is.
        conn.execute("UPDATE messages SET origin = NULL WHERE origin = ''")
        if "origin" in {r["name"] for r in conn.execute("PRAGMA table_info(fts)")}:
            conn.execute("UPDATE fts SET origin = NULL WHERE origin = ''")
        conn.execute("DELETE FROM meta WHERE key='provenance_backfill_pending'")
        conn.execute("DELETE FROM sync_state")
        conn.execute("UPDATE documents SET content_hash = NULL")

    if version < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


def _rebuild_fts_from_messages(conn: sqlite3.Connection) -> None:
    """Repopulate the retrieval index from the verbatim messages already stored.

    Used when the FTS table has to be recreated for a schema change. It keeps an
    upgraded index searchable straight away, including documents whose source
    file has since been deleted, which a rebuild-from-disk would silently lose.
    """
    docs = conn.execute(
        "SELECT doc_id, title, source, project FROM documents"
    ).fetchall()
    for d in docs:
        rows = conn.execute(
            "SELECT seq, role, ts_utc, text, origin FROM messages "
            "WHERE doc_id=? ORDER BY seq", (d["doc_id"],)
        ).fetchall()
        if not rows:
            continue
        msgs = [Message(seq=r["seq"], role=r["role"] or "", text=r["text"] or "",
                        ts_utc=r["ts_utc"] or "", origin=r["origin"] or "")
                for r in rows]
        conn.executemany(
            """INSERT INTO fts (text, title, doc_id, source, project, role,
                                ts_utc, seq, origin)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (p.text, d["title"], d["doc_id"], d["source"], d["project"],
                 p.role, p.ts_utc, p.seq, p.origin)
                for p in util.passages(msgs)
            ],
        )


# ---------------------------------------------------------------------------
# store API
# ---------------------------------------------------------------------------

class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- writes -------------------------------------------------------------

    def existing_hash(self, doc_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT content_hash FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        return row["content_hash"] if row else None

    def document_ref(self, doc_id: str) -> Optional[str]:
        """The ``ref`` of an indexed document, or None if it is not indexed.

        Used to settle ownership when two files could describe the same document:
        a raw export and the readable rendering of it (see sources.notes).
        Deliberately lighter than ``get_document``, which also loads every message.
        """
        row = self.conn.execute(
            "SELECT ref FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        return row["ref"] if row else None

    def set_document_ref(self, doc_id: str, ref: str) -> None:
        """Repoint a document at a different file, keeping its history.

        Used when a raw export is retired in favour of the readable rendering of
        it (features.materialize): the document is the same document, so its
        access and decay history must survive, but the file that owns it changes.
        """
        self.conn.execute("UPDATE documents SET ref=? WHERE doc_id=?", (ref, doc_id))
        self.conn.commit()

    def set_document_project(self, doc_id: str, project: str) -> None:
        """Record which project a document belongs to, keeping its history.

        Needed because a materialized rendering does not re-index its document
        (that would duplicate it, see sources.notes), so writing the file into
        ``<project>/`` would otherwise leave the index still saying the document
        has no project — the folder and the index disagreeing about the same
        conversation, and ``search --project`` unable to find something that is
        visibly filed. Filing is a fact about the document, not about the file, so
        it is set on the row directly.
        """
        self.conn.execute("UPDATE documents SET project=? WHERE doc_id=?",
                          (project, doc_id))
        # The FTS rows carry their own copy of the project — it is what
        # `search --project` filters on and what a hit reports — so updating the
        # document row alone would leave search answering from the stale copy.
        self.conn.execute("UPDATE fts SET project=? WHERE doc_id=?",
                          (project, doc_id))
        self.conn.commit()

    def documents_by_ref(self, ref: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM documents WHERE ref=?", (ref,)
        )]

    def upsert_document(self, doc: Document) -> bool:
        """Insert or replace a document and its messages.

        Returns True if the index changed, False if it was already up to date.
        """
        new_hash = doc.content_hash()
        if self.existing_hash(doc.doc_id) == new_hash:
            return False

        self._delete_rows(doc.doc_id)

        wc = sum(util.word_count(m.text) for m in doc.messages)
        self.conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, source, native_id, title, project, created_utc,
                updated_utc, ref, extra_json, content_hash, msg_count, word_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc.doc_id, doc.source, doc.native_id, doc.title, doc.project,
                doc.created_utc, doc.updated_utc, doc.ref,
                json.dumps(doc.extra, ensure_ascii=False), new_hash,
                len(doc.messages), wc,
            ),
        )
        # The verbatim record, used to reproduce the document.
        self.conn.executemany(
            "INSERT INTO messages (doc_id, seq, role, ts_utc, text, origin) "
            "VALUES (?,?,?,?,?,?)",
            [
                (doc.doc_id, m.seq, m.role, m.ts_utc, m.text, m.origin or "")
                for m in doc.messages
                if m.text and m.text.strip()
            ],
        )
        # The retrieval index, normalised to even passages so documents from
        # different sources compete on the same terms. See util.passages.
        self.conn.executemany(
            """INSERT INTO fts (text, title, doc_id, source, project, role,
                                ts_utc, seq, origin)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (
                    p.text, doc.title, doc.doc_id, doc.source, doc.project,
                    p.role, p.ts_utc, p.seq, p.origin,
                )
                for p in util.passages(doc.messages)
            ],
        )
        return True

    def _delete_rows(self, doc_id: str) -> None:
        self.conn.execute("DELETE FROM fts WHERE doc_id=?", (doc_id,))
        self.conn.execute("DELETE FROM messages WHERE doc_id=?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))

    def delete_document(self, doc_id: str) -> None:
        self._delete_rows(doc_id)

    def commit(self) -> None:
        self.conn.commit()

    # -- schema state -------------------------------------------------------

    def provenance_pending(self) -> bool:
        """True while any message still has no recorded provenance.

        Asked of the data, not of a flag. A migration adds `origin` as NULL and
        an ingest fills it in per document, so "is the backfill done" is exactly
        "is there a row left with a NULL origin" — it becomes false when the last
        message has been re-read, and it cannot be turned off early.

        The flag this replaces was cleared when an ingest *command* returned,
        which is not the same event: sources that scanned zero files still
        counted, so the guard disarmed with more than half the corpus blank and
        nothing left to detect it with.
        """
        return self.conn.execute(
            "SELECT 1 FROM messages WHERE origin IS NULL LIMIT 1").fetchone() is not None

    # -- sync bookkeeping ---------------------------------------------------

    def get_signature(self, source: str, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT signature FROM sync_state WHERE source=? AND key=?", (source, key)
        ).fetchone()
        return row["signature"] if row else None

    def set_signature(self, source: str, key: str, signature: str) -> None:
        self.conn.execute(
            """INSERT INTO sync_state(source,key,signature,updated_at)
               VALUES(?,?,?,datetime('now'))
               ON CONFLICT(source,key) DO UPDATE SET
                 signature=excluded.signature, updated_at=excluded.updated_at""",
            (source, key, signature),
        )

    # -- reads --------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        raw: bool = False,
        sources: Optional[Iterable[str]] = None,
        project: Optional[str] = None,
        origins: Optional[Iterable[str]] = None,
        limit: int = 20,
        include_historical: bool = False,
        record: bool = True,
    ) -> list[dict]:
        """`origins=` constrains provenance (util.ORIGIN_*) in SQL, like `sources=`.

        A passage is labelled with the origin of the messages it covers, typed
        winning a mix (util.passages), so `origins=(ORIGIN_TYPED,)` narrows to
        passages that contain something the human actually typed.
        """
        if raw:
            rows = self._run_match(query, sources, project, limit, include_historical,
                                   origins=origins)
        else:
            # "All terms" is a claim about the *document*, not about one passage.
            #
            # Before passages existed, a note was a single row, so requiring every
            # term in one row and requiring them in one document were the same
            # thing. Once documents are split for ranking they stop being the
            # same, and the strict reading gets it wrong: remembering four things
            # from one meeting would find nothing, because the four terms are
            # spread across four passages. Measured, that cost 9.9 points of
            # top-1 on bag-of-terms queries and 14.3 on Granola meetings.
            #
            # So the term requirement is applied at document level to pick the
            # candidates, while ranking still happens at passage level so the
            # best passage is what surfaces. Falls back to any-term when nothing
            # contains the lot.
            docs = self._docs_with_all_terms(query, sources, project,
                                             include_historical, origins=origins)
            or_match = util.to_fts_query(query, "OR")
            rows = self._run_match(or_match, sources, project, limit,
                                   include_historical, only_docs=docs, origins=origins)
            if not rows:
                rows = self._run_match(or_match, sources, project, limit,
                                       include_historical, origins=origins)
        if record and rows:
            self.record_access({r["doc_id"] for r in rows})
        return rows

    def _docs_with_all_terms(self, query, sources, project,
                             include_historical=False, origins=None) -> Optional[set]:
        """Documents containing every (non-stop-word) term, anywhere within them.

        Returns None when the question does not apply — an empty query, or a
        single term, where the any-term pass is already equivalent — so callers
        can skip the filter rather than treat it as 'no matches'.
        """
        tokens = util.fts_tokens(query)
        kept = [t for t in tokens if util._bare(t) not in util._STOPWORDS]
        tokens = kept or tokens
        if len(tokens) < 2:
            return None

        common: Optional[set] = None
        for tok in tokens:
            where = ["fts MATCH ?"]
            params: list[Any] = [tok]
            if not include_historical:
                where.append("d.active = 1")
            if sources:
                srcs = list(sources)
                where.append("fts.source IN (%s)" % ",".join("?" * len(srcs)))
                params.extend(srcs)
            if project:
                where.append("fts.project = ?")
                params.append(project)
            if origins:
                origs = list(origins)
                where.append("fts.origin IN (%s)" % ",".join("?" * len(origs)))
                params.extend(origs)
            sql = ("SELECT DISTINCT fts.doc_id AS doc_id FROM fts "
                   "JOIN documents d ON d.doc_id = fts.doc_id "
                   f"WHERE {' AND '.join(where)}")
            ids = {r["doc_id"] for r in self.conn.execute(sql, params)}
            common = ids if common is None else (common & ids)
            if not common:
                return None      # nothing has all of them; let the caller widen
        return common or None

    def _run_match(self, match, sources, project, limit, include_historical=False,
                   only_docs=None, origins=None) -> list[dict]:
        if not match.strip():
            return []
        where = ["fts MATCH ?"]
        where_params: list[Any] = [match]
        if not include_historical:
            where.append("d.active = 1")
        if only_docs is not None:
            ids = list(only_docs)
            where.append("fts.doc_id IN (%s)" % ",".join("?" * len(ids)))
            where_params.extend(ids)
        if sources:
            srcs = list(sources)
            where.append("fts.source IN (%s)" % ",".join("?" * len(srcs)))
            where_params.extend(srcs)
        if project:
            where.append("fts.project = ?")
            where_params.append(project)
        if origins:
            origs = list(origins)
            where.append("fts.origin IN (%s)" % ",".join("?" * len(origs)))
            where_params.extend(origs)

        # Params bind in order of appearance, and the penalty sits in SELECT,
        # which precedes WHERE.
        params: list[Any] = [config.SOURCE_CLAUDE_CODE, TRANSCRIPT_RANK_PENALTY]
        params.extend(where_params)

        # bm25 column weights: text=1.0, title=5.0 (title hits rank higher).
        #
        # Two passes, because SQLite refuses to evaluate an FTS auxiliary function
        # such as snippet() in a query that also uses a window function.
        #
        # Pass one ranks and applies the per-document cap. Capping has to happen
        # after scoring but before the LIMIT: otherwise a document with many
        # strong passages consumes the whole page before any other document is
        # considered, which is what made 'inbox drop folder' return three hits
        # from the same session. Doing it in SQL rather than by over-fetching and
        # trimming in Python keeps it exact — a transcript can match hundreds of
        # passages, and any fixed over-fetch would eventually be swamped by one.
        # Three levels, and the nesting is forced: SQLite will not evaluate an FTS
        # auxiliary function (bm25, snippet) in any query that also uses a window
        # function. So bm25 is computed in the innermost plain SELECT, and
        # ROW_NUMBER runs one level out over ordinary columns.
        rank_sql = f"""
            SELECT doc_id, seq, score FROM (
                SELECT doc_id, seq, score,
                       ROW_NUMBER() OVER (
                           PARTITION BY doc_id ORDER BY score
                       ) AS rank_in_doc
                FROM (
                    SELECT
                        fts.doc_id AS doc_id,
                        fts.seq    AS seq,
                        bm25(fts, 1.0, 5.0)
                            * CASE WHEN fts.source = ? THEN ? ELSE 1.0 END AS score
                    FROM fts
                    JOIN documents d ON d.doc_id = fts.doc_id
                    WHERE {" AND ".join(where)}
                )
            )
            WHERE rank_in_doc <= ?
            ORDER BY score
            LIMIT ?
        """
        ranked = self.conn.execute(
            rank_sql, [*params, MAX_HITS_PER_DOC, limit]
        ).fetchall()
        if not ranked:
            return []

        # Pass two hydrates just those passages, with snippets. The MATCH is
        # repeated so snippet() has the query context it needs to highlight.
        scores = {(r["doc_id"], r["seq"]): r["score"] for r in ranked}
        pairs = list(scores.keys())
        placeholders = ",".join("(?,?)" for _ in pairs)
        hydrate_sql = f"""
            SELECT
                fts.doc_id AS doc_id,
                fts.source AS source,
                fts.project AS project,
                fts.role   AS role,
                fts.origin AS origin,
                fts.ts_utc AS ts_utc,
                fts.seq    AS seq,
                d.title    AS title,
                d.created_utc AS created_utc,
                d.updated_utc AS updated_utc,
                d.ref      AS ref,
                snippet(fts, 0, '«', '»', ' … ', 12) AS snippet
            FROM fts
            JOIN documents d ON d.doc_id = fts.doc_id
            WHERE {" AND ".join(where)}
              AND (fts.doc_id, fts.seq) IN (VALUES {placeholders})
        """
        flat: list[Any] = []
        for doc_id, seq in pairs:
            flat.extend((doc_id, seq))
        # No penalty params here: this pass only fetches, the scores come from
        # pass one, so the leading placeholders are the WHERE clause's.
        hydrated = self.conn.execute(hydrate_sql, [*where_params, *flat]).fetchall()

        out = []
        for r in hydrated:
            d = dict(r)
            d["score"] = scores[(d["doc_id"], d["seq"])]
            out.append(d)
        out.sort(key=lambda d: d["score"])
        return out

    def get_document(self, doc_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if not row:
            return None
        doc = dict(row)
        # From `messages`, not `fts`: FTS rows are passages, which merge and split
        # the originals for retrieval. Callers here want the conversation as written.
        msgs = self.conn.execute(
            "SELECT seq, role, ts_utc, text, origin FROM messages WHERE doc_id=? "
            "ORDER BY seq",
            (doc_id,),
        ).fetchall()
        doc["messages"] = [dict(m) for m in msgs]
        return doc

    # -- access tracking & lifecycle (used by decay / synthesis) ------------

    def record_access(self, doc_ids) -> None:
        ids = list(doc_ids)
        if not ids:
            return
        qmarks = ",".join("?" * len(ids))
        self.conn.execute(
            f"UPDATE documents SET accessed_utc = datetime('now'), active = 1 "
            f"WHERE doc_id IN ({qmarks})",
            ids,
        )
        self.conn.commit()

    def set_active(self, doc_id: str, active: bool) -> None:
        self.conn.execute(
            "UPDATE documents SET active=? WHERE doc_id=?", (1 if active else 0, doc_id)
        )
        self.conn.commit()

    def iter_documents(self, include_historical: bool = True) -> list[dict]:
        sql = "SELECT * FROM documents"
        if not include_historical:
            sql += " WHERE active=1"
        return [dict(r) for r in self.conn.execute(sql)]

    def recent_documents(self, since_iso: str, *, by: str = "updated_utc") -> list[dict]:
        col = "updated_utc" if by not in ("updated_utc", "created_utc", "accessed_utc") else by
        return [
            dict(r) for r in self.conn.execute(
                f"SELECT * FROM documents WHERE {col} >= ? ORDER BY {col} DESC", (since_iso,)
            )
        ]

    def stats(self) -> dict:
        out: dict[str, Any] = {}
        out["documents"] = self.conn.execute(
            "SELECT COUNT(*) c FROM documents"
        ).fetchone()["c"]
        out["messages"] = self.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        # Retrieval units, which differ from messages once passages are built.
        out["passages"] = self.conn.execute("SELECT COUNT(*) c FROM fts").fetchone()["c"]
        out["archived"] = self.conn.execute(
            "SELECT COUNT(*) c FROM documents WHERE active=0"
        ).fetchone()["c"]
        out["by_source"] = {
            r["source"]: {"documents": r["docs"], "words": r["words"] or 0}
            for r in self.conn.execute(
                "SELECT source, COUNT(*) docs, SUM(word_count) words "
                "FROM documents GROUP BY source ORDER BY docs DESC"
            )
        }
        row = self.conn.execute(
            "SELECT MIN(created_utc) lo, MAX(updated_utc) hi FROM documents "
            "WHERE created_utc != ''"
        ).fetchone()
        out["earliest"] = row["lo"]
        out["latest"] = row["hi"]
        return out
