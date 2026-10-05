from threading import Lock

from ingestion.beans.decorators import component
from ingestion.config_loader.models import EvaluationConfig
from ingestion.evaluation.interface import StageRecorder
from ingestion.evaluation.models import StageObservation


@component(contract=StageRecorder, name="none")
class NullStageRecorder:
    def record(self, observation: StageObservation) -> None:
        del observation


@component(contract=StageRecorder, name="jsonl")
class JsonlStageRecorder:
    def __init__(self, config: EvaluationConfig) -> None:
        directory = config.recording.directory
        if directory is None:
            raise ValueError("evaluation.recording.directory is required for jsonl recording")
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / "stage-observations.jsonl"
        self._lock = Lock()

    def record(self, observation: StageObservation) -> None:
        with self._lock, self._path.open("a", encoding="utf-8") as stream:
            stream.write(observation.model_dump_json() + "\n")
