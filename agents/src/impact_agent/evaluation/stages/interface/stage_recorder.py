"""Port for recording privacy-safe stage outcomes."""

from typing import Protocol

from impact_agent.domain.models import StageEvaluation


class StageRecorder(Protocol):
    def record(self, run_id: str, result: StageEvaluation) -> None: ...
