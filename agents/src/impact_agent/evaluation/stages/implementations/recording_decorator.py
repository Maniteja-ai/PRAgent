"""Record stage timing and fingerprints without persisting input or output content."""

import hashlib
import json
import time
from dataclasses import dataclass, fields, is_dataclass

from impact_agent.domain.models import StageEvaluation
from impact_agent.domain.state import AgentState
from impact_agent.evaluation.stages.interface.stage_recorder import StageRecorder
from impact_agent.pipeline.interface.pipeline_stage import PipelineStage


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_serialize)


def _serialize(value: object) -> object:
    if is_dataclass(value):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    raise TypeError(f"Cannot fingerprint pipeline value of type {type(value).__name__}")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EvaluationRecordingDecorator:
    stage_name: str
    stage: PipelineStage
    run_id: str
    recorder: StageRecorder

    def __call__(self, state: AgentState) -> AgentState:
        started = time.perf_counter()
        input_hash = _fingerprint(state)
        try:
            result = self.stage(state)
        except Exception as error:
            self._record(
                StageEvaluation(
                    stage=self.stage_name,
                    status="FAILED",
                    duration_ms=self._duration(started),
                    input_sha256=input_hash,
                    error_type=type(error).__name__,
                )
            )
            raise
        self._record(
            StageEvaluation(
                stage=self.stage_name,
                status="COMPLETED",
                duration_ms=self._duration(started),
                input_sha256=input_hash,
                output_sha256=_fingerprint(result),
            )
        )
        return result

    @staticmethod
    def _duration(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    def _record(self, result: StageEvaluation) -> None:
        try:
            self.recorder.record(self.run_id, result)
        except Exception:
            # Optional evaluation recording must not alter analysis behavior.
            pass
