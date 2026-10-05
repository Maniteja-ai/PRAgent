from impact_agent.domain.models import StageEvaluation
from impact_agent.domain.state import AgentState
from impact_agent.evaluation.stages.implementations.recording_decorator import (
    EvaluationRecordingDecorator,
)


class Recorder:
    def __init__(self):
        self.events: list[tuple[str, StageEvaluation]] = []

    def record(self, run_id: str, result: StageEvaluation) -> None:
        self.events.append((run_id, result))


def test_decorator_records_hashes_and_does_not_store_report_content():
    recorder = Recorder()
    state: AgentState = {"run_id": "run-1", "gaps": ("private summary",)}
    decorator = EvaluationRecordingDecorator("analyze", lambda state: state, "run-1", recorder)

    assert decorator(state) is state
    run_id, result = recorder.events[0]
    assert run_id == "run-1"
    assert result.status == "COMPLETED"
    assert len(result.input_sha256) == 64
    assert result.output_sha256 == result.input_sha256
    assert "private summary" not in repr(result)
