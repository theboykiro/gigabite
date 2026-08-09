"""The autonomy ledger: durable state for work that outlives a process.

Every gigabite command until now has been one process, start to exit. That is
fine for retrieval and fatal for autonomy — a chain of work that cannot survive a
process cannot survive a laptop closing, and a system whose only record of what it
did is the conversation it did it in cannot be audited, resumed, or measured.

This module is the missing layer (docs/AUTONOMY.md §3). It records:

  runs        a mission: goal, definition of done, constraints, budget, authority
  steps       the decomposed plan, written down BEFORE it is executed
  decisions   what was chosen, what was rejected, and why
  blockers    what could not be got, and what would unblock it
  artifacts   what the run produced
  audit       every gated action and how it was dispositioned

Three properties are load-bearing, and each exists because of a specific failure:

**`runs.version` is monotonic.** Anything that reads run state and then acts on it
passes the version it read back in; a mismatch raises `StaleVersion` rather than
overwriting a concurrent update. This is the cheap defence against acting on a
snapshot that has since moved.

**Rejected branches are mandatory.** `record_decision` requires `why`. A run that
records only what it did is not auditable, because the part a human needs in order
to disagree is the road not taken.

**The ledger is a separate database from the index.** `reindex` deletes the index
and rebuilds it from the files on disk. Nothing can rebuild a run history, so it
does not live anywhere `reindex` can reach.

The kill switch (§4) is a file, `~/Knowledge/.gigabite/STOP`. Every mutating call
checks it and raises `Halted`. It is a file and not a row so that it works when
the database is locked, can be set by a `touch` from anywhere, and is visible to a
human who is trying to work out why nothing is running.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import config

SCHEMA_VERSION = 2

# Run statuses. `blocked` is distinct from `halted`: blocked means the work hit
# something it cannot get past on its own (see blockers), halted means a human or
# the kill switch stopped it.
RUN_STATUSES = ("planned", "running", "blocked", "halted", "done", "failed")
STEP_STATUSES = ("pending", "running", "done", "failed", "skipped")
BLOCKER_STATUSES = ("open", "resolved", "abandoned")

# Authority levels (docs/AUTONOMY.md §4/§5). Every action class starts at
# `supervised` and is promoted on evidence, never by default.
AUTHORITY_LEVELS = ("passive", "advisory", "supervised", "full")


class LedgerError(Exception):
    """Base class for ledger failures."""


class Halted(LedgerError):
    """Raised when the kill switch is engaged, or the run itself was halted."""


class StaleVersion(LedgerError):
    """Raised when a caller acts on a run state that has since moved."""


class UnknownRun(LedgerError):
    """Raised for a run_id that is not in the ledger."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _unjson(raw: Any) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def new_run_id() -> str:
    """A sortable, human-quotable id: `run_20260809-4f2a1c`."""
    return f"run_{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3)}"


# ---------------------------------------------------------------------------
# kill switch
# ---------------------------------------------------------------------------

def stop_file() -> Path:
    """Resolved at call time — `config.MACHINE_DIR` is a module-level constant
    computed at import, and the tests move the knowledge root underneath it."""
    return config.machine_dir() / "STOP"


def halted() -> bool:
    return stop_file().exists()


def stop_reason() -> str:
    try:
        return stop_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def engage_stop(reason: str = "") -> Path:
    """Set the global kill switch. Idempotent; an existing reason is preserved."""
    path = stop_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"{_now()} {reason}".strip() + "\n", encoding="utf-8")
    return path


def release_stop() -> bool:
    """Clear the kill switch. Returns whether it had been set.

    Deliberately does NOT restart anything. Halted runs stay halted and are
    resumed one at a time, on purpose — a switch that un-halts everything it
    halted is not a safety mechanism, it is a pause button.
    """
    path = stop_file()
    if path.exists():
        path.unlink()
        return True
    return False


def _check_halt() -> None:
    if halted():
        reason = stop_reason()
        raise Halted(f"kill switch engaged: {reason}" if reason else "kill switch engaged")


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else config.machine_dir() / "index" / "ledger.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS runs (
            run_id           TEXT PRIMARY KEY,
            goal             TEXT NOT NULL,
            done_definition  TEXT,
            constraints_json TEXT,
            budget_json      TEXT,
            authority        TEXT NOT NULL DEFAULT 'supervised',
            status           TEXT NOT NULL DEFAULT 'planned',
            project          TEXT,
            -- What this would have taken by hand. The user's estimate at mission
            -- start; the denominator of the only KPI that has been named.
            baseline_minutes REAL,
            -- Attention the user actually spent inside gates and blockers. NOT
            -- wall time: if the run takes two hours while they do something else,
            -- that is the product working (docs/AUTONOMY.md §8).
            human_touch_seconds REAL NOT NULL DEFAULT 0,
            started_utc      TEXT,
            ended_utc        TEXT,
            halt_reason      TEXT,
            version          INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_runs_status  ON runs(status);
        CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_utc);

        CREATE TABLE IF NOT EXISTS steps (
            run_id        TEXT NOT NULL,
            seq           INTEGER NOT NULL,
            kind          TEXT NOT NULL,
            summary       TEXT,
            input_json    TEXT,
            -- The output contract: what this step promises to produce, checked
            -- before the step is allowed to count as done.
            contract_json TEXT,
            output_json   TEXT,
            status        TEXT NOT NULL DEFAULT 'pending',
            attempts      INTEGER NOT NULL DEFAULT 0,
            error         TEXT,
            started_utc   TEXT,
            ended_utc     TEXT,
            PRIMARY KEY (run_id, seq),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS decisions (
            run_id        TEXT NOT NULL,
            seq           INTEGER NOT NULL,
            step_seq      INTEGER,
            question      TEXT NOT NULL,
            chosen        TEXT NOT NULL,
            rejected_json TEXT,
            why           TEXT NOT NULL,
            ts_utc        TEXT NOT NULL,
            PRIMARY KEY (run_id, seq),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS blockers (
            blocker_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id            TEXT NOT NULL,
            step_seq          INTEGER,
            kind              TEXT NOT NULL,
            description       TEXT NOT NULL,
            -- The field that makes a blocker useful rather than an apology.
            what_would_unblock TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'open',
            surfaced_utc      TEXT NOT NULL,
            resolved_utc      TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_blockers_status ON blockers(status);

        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL,
            ref         TEXT NOT NULL,
            kind        TEXT,
            created_utc TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        -- Batched approvals (docs/AUTONOMY.md §4). "Yes, open tickets for this
        -- run" is one answer rather than eleven, so a grant is scoped to a run
        -- and an action class, never to a single call.
        --
        -- Storage only: a grant row is not permission. features.policy decides,
        -- and it ignores grants for hard-refused classes outright — so a forged
        -- row here cannot produce an allow.
        CREATE TABLE IF NOT EXISTS grants (
            grant_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT NOT NULL,
            action_class TEXT NOT NULL,
            note         TEXT,
            granted_utc  TEXT NOT NULL,
            revoked_utc  TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_grants_run ON grants(run_id);

        CREATE TABLE IF NOT EXISTS audit (
            audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT,
            ts_utc       TEXT NOT NULL,
            action_class TEXT NOT NULL,
            action       TEXT NOT NULL,
            disposition  TEXT NOT NULL,
            detail_json  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_run ON audit(run_id);
        CREATE INDEX IF NOT EXISTS idx_audit_ts  ON audit(ts_utc);
        """
    )
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    found = int(row["value"]) if row and str(row["value"]).isdigit() else None

    if found is None:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),)
        )
    elif found > SCHEMA_VERSION:
        # Forward guard. The index has none, and the consequence there is a
        # corrupted rebuild of derived data; here it would be a newer tool's run
        # history read through an older tool's assumptions. Stop instead.
        raise LedgerError(
            f"this ledger was written by a newer gigabite (schema v{found}, this build "
            f"understands v{SCHEMA_VERSION}). Upgrade rather than risk the run history."
        )
    elif found < SCHEMA_VERSION:
        # v1 -> v2 added `grants`, and every table above is CREATE ... IF NOT
        # EXISTS, so the upgrade already happened. Purely additive changes need
        # no migration branch — only a version stamp.
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------

@dataclass
class Run:
    run_id: str
    goal: str
    done_definition: str = ""
    constraints: Any = None
    budget: Any = None
    authority: str = "supervised"
    status: str = "planned"
    project: str = ""
    baseline_minutes: Optional[float] = None
    human_touch_seconds: float = 0.0
    started_utc: str = ""
    ended_utc: str = ""
    halt_reason: str = ""
    version: int = 1

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Run":
        return cls(
            run_id=row["run_id"],
            goal=row["goal"],
            done_definition=row["done_definition"] or "",
            constraints=_unjson(row["constraints_json"]),
            budget=_unjson(row["budget_json"]),
            authority=row["authority"],
            status=row["status"],
            project=row["project"] or "",
            baseline_minutes=row["baseline_minutes"],
            human_touch_seconds=row["human_touch_seconds"] or 0.0,
            started_utc=row["started_utc"] or "",
            ended_utc=row["ended_utc"] or "",
            halt_reason=row["halt_reason"] or "",
            version=row["version"],
        )


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------

class Ledger:
    """All ledger access. Owns a connection; commits on every mutation.

    Mutations commit immediately rather than batching, which is the opposite of
    what the ingest path does and correct for the opposite reason: an ingest that
    is interrupted is re-run from the files, whereas a run that is interrupted has
    to be able to say how far it got.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @classmethod
    def open(cls, db_path: Optional[Path] = None) -> "Ledger":
        return cls(connect(db_path))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- runs ---------------------------------------------------------------

    def start_run(
        self,
        goal: str,
        *,
        done_definition: str = "",
        constraints: Any = None,
        budget: Any = None,
        authority: str = "supervised",
        project: str = "",
        baseline_minutes: Optional[float] = None,
        run_id: Optional[str] = None,
    ) -> Run:
        _check_halt()
        if authority not in AUTHORITY_LEVELS:
            raise LedgerError(f"unknown authority {authority!r}; expected one of {AUTHORITY_LEVELS}")
        if not goal.strip():
            raise LedgerError("a run needs a goal")
        rid = run_id or new_run_id()
        self.conn.execute(
            "INSERT INTO runs (run_id, goal, done_definition, constraints_json, budget_json,"
            " authority, status, project, baseline_minutes, started_utc, version)"
            " VALUES (?,?,?,?,?,?,'running',?,?,?,1)",
            (rid, goal.strip(), done_definition, _json(constraints), _json(budget),
             authority, project, baseline_minutes, _now()),
        )
        self.conn.commit()
        self.audit(rid, "run", "start", "auto", {"goal": goal, "authority": authority})
        return self.get_run(rid)

    def get_run(self, run_id: str) -> Optional[Run]:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return Run.from_row(row) if row else None

    def _require(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        if run is None:
            raise UnknownRun(run_id)
        return run

    def list_runs(self, *, status: str = "", limit: int = 20) -> list:
        sql = "SELECT * FROM runs"
        params: list = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY started_utc DESC LIMIT ?"
        params.append(limit)
        return [Run.from_row(r) for r in self.conn.execute(sql, params)]

    def update_run(self, run_id: str, *, expected_version: Optional[int] = None, **fields) -> Run:
        """Apply field updates and bump `version`.

        `expected_version` is how a caller says "I read the run at version N and
        my update assumes nothing has changed since". Omitting it is a blind
        write, which is fine for a single-threaded caller and wrong for anything
        that read, thought, and is now acting.
        """
        _check_halt()
        run = self._require(run_id)
        if expected_version is not None and expected_version != run.version:
            raise StaleVersion(
                f"{run_id} is at version {run.version}, caller read {expected_version}"
            )
        allowed = {
            "goal", "done_definition", "authority", "status", "project",
            "baseline_minutes", "halt_reason", "ended_utc",
        }
        json_fields = {"constraints": "constraints_json", "budget": "budget_json"}
        sets, params = [], []
        for key, value in fields.items():
            if key in json_fields:
                sets.append(f"{json_fields[key]}=?")
                params.append(_json(value))
            elif key in allowed:
                if key == "status" and value not in RUN_STATUSES:
                    raise LedgerError(f"unknown run status {value!r}")
                if key == "authority" and value not in AUTHORITY_LEVELS:
                    raise LedgerError(f"unknown authority {value!r}")
                sets.append(f"{key}=?")
                params.append(value)
            else:
                raise LedgerError(f"{key!r} is not an updatable run field")
        sets.append("version=version+1")
        params.append(run_id)
        self.conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id=?", params)
        self.conn.commit()
        return self._require(run_id)

    def finish_run(self, run_id: str, status: str = "done") -> Run:
        if status not in RUN_STATUSES:
            raise LedgerError(f"unknown run status {status!r}")
        run = self._require(run_id)
        self.conn.execute(
            "UPDATE runs SET status=?, ended_utc=?, version=version+1 WHERE run_id=?",
            (status, _now(), run_id),
        )
        self.conn.commit()
        self.audit(run_id, "run", "finish", status)
        return self._require(run_id)

    def halt_run(self, run_id: str, reason: str = "") -> Run:
        """Stop one run. Unlike the other mutators this does NOT check the kill
        switch — halting has to work when the switch is already engaged."""
        self._require(run_id)
        self.conn.execute(
            "UPDATE runs SET status='halted', halt_reason=?, ended_utc=?, version=version+1"
            " WHERE run_id=?",
            (reason, _now(), run_id),
        )
        self.conn.commit()
        self.audit(run_id, "run", "halt", "halted", {"reason": reason})
        return self._require(run_id)

    def halt_all(self, reason: str = "") -> list:
        """Engage the kill switch and halt everything currently in flight."""
        engage_stop(reason)
        live = [r["run_id"] for r in self.conn.execute(
            "SELECT run_id FROM runs WHERE status IN ('planned','running','blocked')"
        )]
        for rid in live:
            self.halt_run(rid, reason or "global stop")
        return live

    def add_human_time(self, run_id: str, seconds: float) -> Run:
        """Record attention the user spent on this run — gates, blockers, review.

        This is the number that gets subtracted from the baseline, and the one
        that makes the cost of oversight visible when arguing about whether an
        action class has earned promotion (docs/AUTONOMY.md §5, §8).
        """
        if seconds < 0:
            raise LedgerError("human time cannot be negative")
        self._require(run_id)
        self.conn.execute(
            "UPDATE runs SET human_touch_seconds=human_touch_seconds+?, version=version+1"
            " WHERE run_id=?",
            (float(seconds), run_id),
        )
        self.conn.commit()
        return self._require(run_id)

    # -- steps --------------------------------------------------------------

    def add_step(
        self,
        run_id: str,
        kind: str,
        *,
        summary: str = "",
        input: Any = None,
        contract: Any = None,
    ) -> int:
        """Append a planned step. Returns its seq.

        Steps are written before they are executed, on purpose: a plan that only
        exists once it has happened is a log, not a plan, and cannot be reviewed
        or resumed.
        """
        _check_halt()
        self._require(run_id)
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS next FROM steps WHERE run_id=?", (run_id,)
        ).fetchone()
        seq = int(row["next"])
        self.conn.execute(
            "INSERT INTO steps (run_id, seq, kind, summary, input_json, contract_json, status)"
            " VALUES (?,?,?,?,?,?,'pending')",
            (run_id, seq, kind, summary, _json(input), _json(contract)),
        )
        self.conn.commit()
        return seq

    def start_step(self, run_id: str, seq: int) -> None:
        _check_halt()
        run = self._require(run_id)
        if run.status == "halted":
            raise Halted(f"{run_id} is halted: {run.halt_reason}")
        self.conn.execute(
            "UPDATE steps SET status='running', attempts=attempts+1, started_utc=?"
            " WHERE run_id=? AND seq=?",
            (_now(), run_id, seq),
        )
        self.conn.commit()

    def finish_step(self, run_id: str, seq: int, *, output: Any = None,
                    status: str = "done") -> None:
        if status not in STEP_STATUSES:
            raise LedgerError(f"unknown step status {status!r}")
        self.conn.execute(
            "UPDATE steps SET status=?, output_json=?, ended_utc=? WHERE run_id=? AND seq=?",
            (status, _json(output), _now(), run_id, seq),
        )
        self.conn.commit()

    def fail_step(self, run_id: str, seq: int, error: str) -> int:
        """Mark a step failed and return how many attempts it has now had —
        which is what a retry ladder decides on."""
        self.conn.execute(
            "UPDATE steps SET status='failed', error=?, ended_utc=? WHERE run_id=? AND seq=?",
            (error, _now(), run_id, seq),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT attempts FROM steps WHERE run_id=? AND seq=?", (run_id, seq)
        ).fetchone()
        return int(row["attempts"]) if row else 0

    def steps(self, run_id: str) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run_id,)
        )]

    # -- decisions ----------------------------------------------------------

    def record_decision(
        self,
        run_id: str,
        question: str,
        chosen: str,
        why: str,
        *,
        rejected: Any = None,
        step_seq: Optional[int] = None,
    ) -> int:
        """`why` is required and non-empty. See the module docstring."""
        _check_halt()
        self._require(run_id)
        if not why.strip():
            raise LedgerError("a decision without a reason is not a decision; `why` is required")
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS next FROM decisions WHERE run_id=?", (run_id,)
        ).fetchone()
        seq = int(row["next"])
        self.conn.execute(
            "INSERT INTO decisions (run_id, seq, step_seq, question, chosen, rejected_json,"
            " why, ts_utc) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, seq, step_seq, question, chosen, _json(rejected), why.strip(), _now()),
        )
        self.conn.commit()
        return seq

    def decisions(self, run_id: str) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM decisions WHERE run_id=? ORDER BY seq", (run_id,)
        )]

    # -- blockers -----------------------------------------------------------

    def open_blocker(
        self,
        run_id: str,
        kind: str,
        description: str,
        what_would_unblock: str,
        *,
        step_seq: Optional[int] = None,
    ) -> int:
        """Park something the run cannot get past.

        Opening a blocker does not stop the run: everything that does not depend
        on it still runs, and the parked set is surfaced once at the end
        (docs/AUTONOMY.md §6). `what_would_unblock` is required because a blocker
        the user cannot act on is just an apology with a timestamp.
        """
        self._require(run_id)
        if not what_would_unblock.strip():
            raise LedgerError("a blocker must say what would unblock it")
        cur = self.conn.execute(
            "INSERT INTO blockers (run_id, step_seq, kind, description, what_would_unblock,"
            " status, surfaced_utc) VALUES (?,?,?,?,?,'open',?)",
            (run_id, step_seq, kind, description, what_would_unblock.strip(), _now()),
        )
        self.conn.commit()
        self.audit(run_id, "blocker", kind, "open", {"description": description})
        return int(cur.lastrowid)

    def resolve_blocker(self, blocker_id: int, status: str = "resolved") -> None:
        if status not in BLOCKER_STATUSES:
            raise LedgerError(f"unknown blocker status {status!r}")
        self.conn.execute(
            "UPDATE blockers SET status=?, resolved_utc=? WHERE blocker_id=?",
            (status, _now(), blocker_id),
        )
        self.conn.commit()

    def blockers(self, *, run_id: str = "", status: str = "open") -> list:
        sql = "SELECT * FROM blockers WHERE 1=1"
        params: list = []
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY surfaced_utc"
        return [dict(r) for r in self.conn.execute(sql, params)]

    # -- grants -------------------------------------------------------------
    #
    # Storage for batched approvals. Deliberately dumb: it records that a human
    # said yes to a class for a run, and nothing here decides anything. The
    # decision lives in features.policy, which is what makes a hand-written grant
    # row for a hard-refused class worthless.

    def grant(self, run_id: str, action_class: str, note: str = "") -> int:
        self._require(run_id)
        cur = self.conn.execute(
            "INSERT INTO grants (run_id, action_class, note, granted_utc) VALUES (?,?,?,?)",
            (run_id, action_class, note, _now()),
        )
        self.conn.commit()
        self.audit(run_id, action_class, "grant", "approved", {"note": note})
        return int(cur.lastrowid)

    def revoke_grant(self, run_id: str, action_class: str) -> int:
        """Revoke every live grant for a class on a run. Returns how many."""
        cur = self.conn.execute(
            "UPDATE grants SET revoked_utc=? WHERE run_id=? AND action_class=?"
            " AND revoked_utc IS NULL",
            (_now(), run_id, action_class),
        )
        self.conn.commit()
        if cur.rowcount:
            self.audit(run_id, action_class, "grant", "revoked")
        return cur.rowcount

    def has_grant(self, run_id: str, action_class: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM grants WHERE run_id=? AND action_class=? AND revoked_utc IS NULL"
            " LIMIT 1",
            (run_id, action_class),
        ).fetchone()
        return row is not None

    def grants(self, run_id: str, *, live_only: bool = True) -> list:
        sql = "SELECT * FROM grants WHERE run_id=?"
        if live_only:
            sql += " AND revoked_utc IS NULL"
        sql += " ORDER BY grant_id"
        return [dict(r) for r in self.conn.execute(sql, (run_id,))]

    # -- artifacts + audit --------------------------------------------------

    def record_artifact(self, run_id: str, ref: str, kind: str = "") -> int:
        self._require(run_id)
        cur = self.conn.execute(
            "INSERT INTO artifacts (run_id, ref, kind, created_utc) VALUES (?,?,?,?)",
            (run_id, ref, kind, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def artifacts(self, run_id: str) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_id", (run_id,)
        )]

    def audit(self, run_id: Optional[str], action_class: str, action: str,
              disposition: str, detail: Any = None) -> int:
        """Record a gated action and how it was dispositioned.

        Never raises on the kill switch: an audit entry describes something that
        already happened, and refusing to write it would lose the record of the
        very actions a stop is most likely to be about.
        """
        cur = self.conn.execute(
            "INSERT INTO audit (run_id, ts_utc, action_class, action, disposition, detail_json)"
            " VALUES (?,?,?,?,?,?)",
            (run_id, _now(), action_class, action, disposition, _json(detail)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def audit_trail(self, *, run_id: str = "", limit: int = 50) -> list:
        sql = "SELECT * FROM audit"
        params: list = []
        if run_id:
            sql += " WHERE run_id=?"
            params.append(run_id)
        sql += " ORDER BY audit_id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    # -- reporting ----------------------------------------------------------

    def summary(self, run_id: str) -> dict:
        """Everything about one run, including the hours-saved arithmetic.

        `hours_saved` is `baseline - human_touch` and deliberately ignores wall
        time (docs/AUTONOMY.md §8). It is None when no baseline was given, rather
        than 0 — an unmeasured run must not read as a run that saved nothing.
        """
        run = self._require(run_id)
        steps = self.steps(run_id)
        touch_min = run.human_touch_seconds / 60.0
        saved = None
        if run.baseline_minutes is not None:
            saved = round((run.baseline_minutes - touch_min) / 60.0, 3)
        wall = None
        if run.started_utc and run.ended_utc:
            try:
                start = datetime.fromisoformat(run.started_utc)
                end = datetime.fromisoformat(run.ended_utc)
                wall = round((end - start).total_seconds() / 60.0, 2)
            except ValueError:
                wall = None
        return {
            "run": run,
            "steps": steps,
            "step_counts": {s: sum(1 for x in steps if x["status"] == s) for s in STEP_STATUSES},
            "decisions": self.decisions(run_id),
            "blockers": self.blockers(run_id=run_id, status=""),
            "open_blockers": self.blockers(run_id=run_id),
            "artifacts": self.artifacts(run_id),
            "human_touch_minutes": round(touch_min, 2),
            "wall_minutes": wall,
            "hours_saved": saved,
        }


def open_ledger(db_path: Optional[Path] = None) -> Ledger:
    """Convenience for callers that do not want to import the class."""
    return Ledger.open(db_path)


# Kept out of the class: the kill switch has to be checkable by anything, whether
# or not it holds a ledger connection.
__all__ = [
    "Ledger", "Run", "open_ledger", "connect",
    "halted", "engage_stop", "release_stop", "stop_file", "stop_reason",
    "Halted", "StaleVersion", "UnknownRun", "LedgerError",
    "RUN_STATUSES", "STEP_STATUSES", "BLOCKER_STATUSES", "AUTHORITY_LEVELS",
    "new_run_id",
]
