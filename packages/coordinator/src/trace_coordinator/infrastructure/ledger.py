"""Transactional attempt accounting, independent of graph checkpoint timing.

Reserve BEFORE dispatch. Unfinished attempts are never replayed automatically.
An operation ID identifies one logical call; each actual retry gets its own row.
"""

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from trace_coordinator.config import CallLimits
from trace_coordinator.domain.contracts import AuditEventPayload, CallAttemptPayload, CallUsagePayload
from trace_coordinator.domain.errors import LimitReached, RunMismatch, UncertainExecution


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class CallLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, started REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (
                    run TEXT NOT NULL, operation TEXT NOT NULL, attempt INTEGER NOT NULL,
                    agent TEXT NOT NULL, tool TEXT NOT NULL, input_hash TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, retryable INTEGER NOT NULL DEFAULT 0,
                    started REAL NOT NULL, ended REAL,
                    PRIMARY KEY (run, operation, attempt));
                CREATE INDEX IF NOT EXISTS counts ON calls(run, agent, tool);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run TEXT NOT NULL, kind TEXT NOT NULL,
                    detail TEXT NOT NULL, created REAL NOT NULL);
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, run: str, fingerprint: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT fingerprint FROM runs WHERE id=?", (run,)).fetchone()
            if row and row[0] != fingerprint:
                raise RunMismatch("Run inputs, configuration or implementation changed; use a new run ID")
            db.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?)", (run, fingerprint, time.time()))

    def event(self, run: str, kind: str, detail: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(run,kind,detail,created) VALUES (?,?,?,?)",
                (run, kind, detail, time.time()),
            )

    def event_once(self, run: str, kind: str, detail: str) -> None:
        with self.connect() as db:
            exists = db.execute(
                "SELECT 1 FROM events WHERE run=? AND kind=? AND detail=? LIMIT 1",
                (run, kind, detail),
            ).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO events(run,kind,detail,created) VALUES (?,?,?,?)",
                    (run, kind, detail, time.time()),
                )

    def reserve(
        self,
        run: str,
        operation: str,
        attempt: int,
        agent: str,
        tool: str,
        arguments: object,
        limits: CallLimits,
    ) -> CallAttemptPayload | None:
        hashed = digest(arguments)
        denial = None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute("SELECT started FROM runs WHERE id=?", (run,)).fetchone()
            if not owner:
                raise RunMismatch("Run has not been registered")
            prior = db.execute(
                "SELECT * FROM calls WHERE run=? AND operation=? AND attempt=?",
                (run, operation, attempt),
            ).fetchone()
            if prior:
                if (prior["agent"], prior["tool"], prior["input_hash"]) != (agent, tool, hashed):
                    raise RunMismatch("Operation identity reused with different arguments")
                if prior["status"] == "STARTED":
                    raise UncertainExecution(
                        f"Unfinished {tool} attempt requires reconciliation; not repeated"
                    )
                return cast(CallAttemptPayload, dict(prior))
            count = db.execute(
                "SELECT COUNT(*) FROM calls WHERE run=? AND agent=? AND tool=?", (run, agent, tool)
            ).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM calls WHERE run=?", (run,)).fetchone()[0]
            if count >= limits.limit_for(agent, tool):
                denial = f"{agent}/{tool} reached its {limits.limit_for(agent, tool)}-attempt limit"
            elif total >= limits.total_calls:
                denial = f"Run reached its {limits.total_calls}-attempt total limit"
            elif self._active_elapsed(db, run, owner[0], time.time()) >= limits.max_run_seconds:
                denial = "Run deadline exceeded"
            if denial:
                db.execute(
                    "INSERT INTO events(run,kind,detail,created) VALUES (?,?,?,?)",
                    (run, "LIMIT_REACHED", denial, time.time()),
                )
            else:
                db.execute(
                    "INSERT INTO calls(run,operation,attempt,agent,tool,input_hash,status,started) "
                    "VALUES (?,?,?,?,?,?,'STARTED',?)",
                    (run, operation, attempt, agent, tool, hashed, time.time()),
                )
        if denial:
            raise LimitReached(denial)
        return None

    @staticmethod
    def _active_elapsed(db: sqlite3.Connection, run: str, started: float, now: float) -> float:
        paused = db.execute(
            "SELECT created FROM events WHERE run=? AND kind='REVIEW_WINDOW_OPENED' ORDER BY id LIMIT 1",
            (run,),
        ).fetchone()
        if not paused:
            return now - started
        resumed = db.execute(
            "SELECT created FROM events WHERE run=? AND kind='FOLLOW_UP_VERIFICATION_CREATED' "
            "ORDER BY id LIMIT 1",
            (run,),
        ).fetchone()
        pause_ended = resumed[0] if resumed else now
        return float(now - started - max(0, pause_ended - paused[0]))

    def active_elapsed(self, run: str) -> float:
        with self.connect() as db:
            owner = db.execute("SELECT started FROM runs WHERE id=?", (run,)).fetchone()
            if not owner:
                raise RunMismatch("Run has not been registered")
            return self._active_elapsed(db, run, owner[0], time.time())

    def finish(
        self,
        run: str,
        operation: str,
        attempt: int,
        status: str,
        result: object,
        retryable: bool = False,
    ) -> None:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE calls SET status=?,result=?,retryable=?,ended=? "
                "WHERE run=? AND operation=? AND attempt=? AND status='STARTED'",
                (status, canonical(result), int(retryable), time.time(), run, operation, attempt),
            )
            if cursor.rowcount != 1:
                raise RunMismatch("Attempt is not pending")

    def usage(self, run: str) -> list[CallUsagePayload]:
        with self.connect() as db:
            return [
                cast(CallUsagePayload, dict(row))
                for row in db.execute(
                    "SELECT agent,tool,COUNT(*) AS attempts, "
                    "SUM(status='SUCCEEDED') AS succeeded,SUM(status='FAILED') AS failed, "
                    "SUM(status='STARTED') AS uncertain FROM calls WHERE run=? "
                    "GROUP BY agent,tool ORDER BY agent,tool",
                    (run,),
                )
            ]

    def events(self, run: str) -> list[AuditEventPayload]:
        with self.connect() as db:
            return [
                cast(AuditEventPayload, dict(row))
                for row in db.execute(
                    "SELECT kind,detail,created FROM events WHERE run=? ORDER BY id", (run,)
                )
            ]

    def latest_event(self, run: str, kind: str) -> AuditEventPayload | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT kind,detail,created FROM events WHERE run=? AND kind=? ORDER BY id DESC LIMIT 1",
                (run, kind),
            ).fetchone()
            return cast(AuditEventPayload, dict(row)) if row else None
