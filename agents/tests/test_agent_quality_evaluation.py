from evaluation.agent_quality.run_quality_eval import _is_evaluable_run_status


def test_quality_evaluation_accepts_successful_runs_with_or_without_gaps():
    assert _is_evaluable_run_status("COMPLETED")
    assert _is_evaluable_run_status("COMPLETED_WITH_GAPS")
    assert not _is_evaluable_run_status("FAILED")
    assert not _is_evaluable_run_status("RUNNING")
