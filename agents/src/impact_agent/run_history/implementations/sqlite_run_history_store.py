"""SQLite persistence for final reports and privacy-safe stage evaluations."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from pydantic import TypeAdapter

from impact_agent.domain.models import AgentReport, StageEvaluation
from impact_agent.evaluation.stages.interface.stage_recorder import StageRecorder
from impact_agent.run_history.interface.run_store import RunStore

_REPORT_ADAPTER = TypeAdapter(AgentReport)


class SQLiteRunHistoryStore(RunStore, StageRecorder):
    """Store each report by stable run ID and stage events without raw prompt contents."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path.resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS agent_runs("
                "run_id TEXT PRIMARY KEY, status TEXT NOT NULL, report_json TEXT NOT NULL, "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS stage_evaluations("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, stage TEXT NOT NULL, "
                "status TEXT NOT NULL, duration_ms INTEGER NOT NULL, input_sha256 TEXT NOT NULL, "
                "output_sha256 TEXT, error_type TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS stage_evaluations_run_id_idx "
                "ON stage_evaluations(run_id, id)"
            )
            connection.commit()

    def save(self, report: AgentReport) -> None:
        report_json = json.dumps(asdict(report), sort_keys=True, separators=(",", ":"))
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            connection.execute(
                "INSERT INTO agent_runs(run_id,status,report_json) VALUES(?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, "
                "report_json=excluded.report_json, updated_at=CURRENT_TIMESTAMP",
                (report.run_id, report.status.value, report_json),
            )
            connection.commit()

    def load(self, run_id: str) -> AgentReport | None:
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            row = connection.execute(
                "SELECT report_json FROM agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return _REPORT_ADAPTER.validate_python(json.loads(row[0]))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Stored report is malformed for run {run_id}") from error

    def record(self, run_id: str, result: StageEvaluation) -> None:
        with closing(sqlite3.connect(self._path, timeout=30)) as connection:
            connection.execute(
                "INSERT INTO stage_evaluations("
                "run_id,stage,status,duration_ms,input_sha256,output_sha256,error_type) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    result.stage,
                    result.status,
                    result.duration_ms,
                    result.input_sha256,
                    result.output_sha256,
                    result.error_type,
                ),
            )
            connection.commit()
