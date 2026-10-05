"""Durable, idempotent queue with atomic leases and bounded retries."""

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Literal

from impact_agent.domain.models import PullRequestRef, WebhookJob, WebhookJobResult
from impact_agent.webhook.interface.webhook_job_handler import WebhookDeliveryConflict


class SQLiteWebhookJobQueue:
    """Persist PR references only; raw webhook payloads and diffs are never stored here."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path.resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS webhook_jobs("
                "delivery_id TEXT PRIMARY KEY, repository TEXT NOT NULL, pr_number INTEGER NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "run_id TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, "
                "lease_until INTEGER, lease_token TEXT, last_error_type TEXT)"
            )
            self._add_legacy_columns(connection)
            connection.execute("UPDATE webhook_jobs SET run_id=delivery_id WHERE run_id=''")
            connection.commit()

    def submit(self, delivery_id: str, reference: PullRequestRef) -> WebhookJobResult:
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO webhook_jobs"
                "(delivery_id,repository,pr_number,status,run_id) VALUES(?,?,?,'QUEUED',?)",
                (delivery_id, reference.repository, reference.number, str(uuid.uuid4())),
            )
            row = connection.execute(
                "SELECT repository,pr_number FROM webhook_jobs WHERE delivery_id=?",
                (delivery_id,),
            ).fetchone()
            if row != (reference.repository, reference.number):
                connection.rollback()
                raise WebhookDeliveryConflict(
                    "Delivery ID is already associated with another pull request"
                )
            connection.commit()
        return WebhookJobResult("QUEUED" if cursor.rowcount == 1 else "DUPLICATE")

    def claim_next(self, lease_seconds: int) -> WebhookJob | None:
        """Atomically claim the oldest available job; expired worker leases are recoverable."""
        token = str(uuid.uuid4())
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT delivery_id,repository,pr_number,run_id,attempts FROM webhook_jobs "
                "WHERE status='QUEUED' OR (status='PROCESSING' AND lease_until<=unixepoch()) "
                "ORDER BY created_at,delivery_id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                "UPDATE webhook_jobs SET status='PROCESSING',attempts=attempts+1,"
                "lease_until=unixepoch()+?,lease_token=? WHERE delivery_id=?",
                (lease_seconds, token, row["delivery_id"]),
            )
            connection.commit()
            return WebhookJob(
                delivery_id=row["delivery_id"],
                reference=PullRequestRef(row["repository"], row["pr_number"]),
                run_id=row["run_id"],
                attempt=row["attempts"] + 1,
                lease_token=token,
            )

    def complete(self, delivery_id: str, lease_token: str) -> None:
        self._finish(delivery_id, lease_token, "COMPLETED", None)

    def fail(
        self,
        delivery_id: str,
        lease_token: str,
        error_type: str,
        max_attempts: int,
    ) -> Literal["REQUEUED", "FAILED"]:
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempts FROM webhook_jobs WHERE delivery_id=? AND status='PROCESSING' "
                "AND lease_token=?",
                (delivery_id, lease_token),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ValueError("Job lease is no longer owned by this worker")
            status: Literal["REQUEUED", "FAILED"] = (
                "REQUEUED" if row[0] < max_attempts else "FAILED"
            )
            job_status = "QUEUED" if status == "REQUEUED" else "FAILED"
            connection.execute(
                "UPDATE webhook_jobs SET status=?,last_error_type=?,lease_until=NULL,lease_token=NULL "
                "WHERE delivery_id=?",
                (job_status, error_type[:100], delivery_id),
            )
            connection.commit()
            return status

    @staticmethod
    def _add_legacy_columns(connection: sqlite3.Connection) -> None:
        """Allow databases created by the first intake-only version to upgrade in place."""
        columns = {row[1] for row in connection.execute("PRAGMA table_info(webhook_jobs)")}
        migrations = {
            "run_id": "TEXT NOT NULL DEFAULT ''",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "lease_until": "INTEGER",
            "lease_token": "TEXT",
            "last_error_type": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE webhook_jobs ADD COLUMN {name} {definition}")

    def _finish(
        self, delivery_id: str, lease_token: str, status: str, error_type: str | None
    ) -> None:
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            cursor = connection.execute(
                "UPDATE webhook_jobs SET status=?,last_error_type=?,lease_until=NULL,lease_token=NULL "
                "WHERE delivery_id=? AND status='PROCESSING' AND lease_token=?",
                (status, error_type, delivery_id, lease_token),
            )
            if cursor.rowcount != 1:
                raise ValueError("Job lease is no longer owned by this worker")
            connection.commit()
