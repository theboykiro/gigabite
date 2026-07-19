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
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config, util

SCHEMA_VERSION = 1


@dataclass
class Message:
    seq: int
    role: str
    text: str
    ts_utc: str = ""


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

    @property
    def doc_id(self) -> str:
        return util.doc_id(self.source, self.native_id)

    def content_hash(self) -> str:
        h = hashlib.sha1()
        h.update(self.title.encode("utf-8"))
        for m in self.messages:
            h.update(b"\x00")
            h.update(f"{m.seq}|{m.role}|{m.ts_utc}|".encode("utf-8"))
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
    init_schema(conn)
    return conn


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
            word_count   INTEGER DEFAULT 0
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

        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
            text,
            title,
            doc_id  UNINDEXED,
            source  UNINDEXED,
            project UNINDEXED,
            role    UNINDEXED,
            ts_utc  UNINDEXED,
            seq     UNINDEXED,
            tokenize = 'porter unicode61'
        );
        """
    )
    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


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
        self.conn.executemany(
            """INSERT INTO fts (text, title, doc_id, source, project, role, ts_utc, seq)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (
                    m.text, doc.title, doc.doc_id, doc.source, doc.project,
                    m.role, m.ts_utc, m.seq,
                )
                for m in doc.messages
                if m.text and m.text.strip()
            ],
        )
        return True

    def _delete_rows(self, doc_id: str) -> None:
        self.conn.execute("DELETE FROM fts WHERE doc_id=?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))

    def delete_document(self, doc_id: str) -> None:
        self._delete_rows(doc_id)

    def commit(self) -> None:
        self.conn.commit()

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
        limit: int = 20,
    ) -> list[dict]:
        match = query if raw else util.to_fts_query(query)
        if not match.strip():
            return []

        where = ["fts MATCH ?"]
        params: list[Any] = [match]
        if sources:
            srcs = list(sources)
            where.append("fts.source IN (%s)" % ",".join("?" * len(srcs)))
            params.extend(srcs)
        if project:
            where.append("fts.project = ?")
            params.append(project)

        # bm25 column weights: text=1.0, title=5.0 (title hits rank higher)
        sql = f"""
            SELECT
                fts.doc_id AS doc_id,
                fts.source AS source,
                fts.project AS project,
                fts.role   AS role,
                fts.ts_utc AS ts_utc,
                fts.seq    AS seq,
                d.title    AS title,
                d.created_utc AS created_utc,
                d.updated_utc AS updated_utc,
                d.ref      AS ref,
                snippet(fts, 0, '«', '»', ' … ', 12) AS snippet,
                bm25(fts, 1.0, 5.0) AS score
            FROM fts
            JOIN documents d ON d.doc_id = fts.doc_id
            WHERE {" AND ".join(where)}
            ORDER BY score
            LIMIT ?
        """
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, doc_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if not row:
            return None
        doc = dict(row)
        msgs = self.conn.execute(
            "SELECT seq, role, ts_utc, text FROM fts WHERE doc_id=? ORDER BY seq",
            (doc_id,),
        ).fetchall()
        doc["messages"] = [dict(m) for m in msgs]
        return doc

    def stats(self) -> dict:
        out: dict[str, Any] = {}
        out["documents"] = self.conn.execute(
            "SELECT COUNT(*) c FROM documents"
        ).fetchone()["c"]
        out["messages"] = self.conn.execute("SELECT COUNT(*) c FROM fts").fetchone()["c"]
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
