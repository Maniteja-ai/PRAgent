from typing import Protocol

from ingestion.evaluation.models import StageObservation


class StageRecorder(Protocol):
    def record(self, observation: StageObservation) -> None: ...
