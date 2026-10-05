"""No-op evaluation recorder used when stage recording is disabled by configuration."""

from impact_agent.domain.models import StageEvaluation
from impact_agent.evaluation.stages.interface.stage_recorder import StageRecorder


class DisabledStageRecorder(StageRecorder):
    def record(self, run_id: str, result: StageEvaluation) -> None:
        return None
