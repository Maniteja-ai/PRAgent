import sqlite3

from impact_agent.domain.models import (
    AgentReport,
    BehaviorResult,
    Evidence,
    Finding,
    StageEvaluation,
)
from impact_agent.domain.pipeline_enums import ReportStatus
from impact_agent.run_history.implementations.sqlite_run_history_store import SQLiteRunHistoryStore


def test_store_round_trips_report_and_stage_hashes_without_raw_inputs(tmp_path):
    store = SQLiteRunHistoryStore(tmp_path / "history.sqlite3")
    report = AgentReport(
        run_id="run-1",
        status=ReportStatus.COMPLETED_WITH_GAPS,
        summary="Cart totals can change.",
        findings=(Finding("Totals", "Recalculate the display.", ("evidence-1",)),),
        evidence=(Evidence("evidence-1", "checkout.md", "voucher rules", "hash"),),
        behavior_results=(BehaviorResult("voucher", "NOT_RUN", "Browser disabled."),),
        gaps=("UI exploration is disabled",),
        rendered_report="# Impact report",
    )
    stage = StageEvaluation("Retrieve knowledge", "COMPLETED", 12, "input-hash", "output-hash")

    store.record("run-1", stage)
    store.save(report)

    assert store.load("run-1") == report
    assert store.load("missing") is None
    with sqlite3.connect(tmp_path / "history.sqlite3") as connection:
        rows = connection.execute(
            "SELECT stage,input_sha256,output_sha256 FROM stage_evaluations"
        ).fetchall()
        report_json = connection.execute("SELECT report_json FROM agent_runs").fetchone()[0]
    assert rows == [("Retrieve knowledge", "input-hash", "output-hash")]
    assert "voucher rules" in report_json


def test_saving_same_run_id_updates_one_report_row(tmp_path):
    store = SQLiteRunHistoryStore(tmp_path / "history.sqlite3")
    report = AgentReport("run-1", ReportStatus.COMPLETED, "first", (), (), (), ())
    updated = AgentReport("run-1", ReportStatus.COMPLETED, "updated", (), (), (), ())

    store.save(report)
    store.save(updated)

    assert store.load("run-1") == updated
    with sqlite3.connect(tmp_path / "history.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 1
