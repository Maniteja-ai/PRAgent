import hashlib
from collections.abc import Callable
from functools import wraps
from time import monotonic
from typing import Any, TypeVar, cast

from ingestion.evaluation.interface import StageRecorder
from ingestion.evaluation.models import StageObservation

ResultT = TypeVar("ResultT")


def record_stage(stage: str) -> Callable[[Callable[..., ResultT]], Callable[..., ResultT]]:
    def decorate(function: Callable[..., ResultT]) -> Callable[..., ResultT]:
        @wraps(function)
        def wrapped(owner: Any, *args: Any, **kwargs: Any) -> ResultT:
            recorder = cast(StageRecorder, owner.stage_recorder)
            run_id = cast(str, owner.run_id)
            started = monotonic()
            try:
                result = function(owner, *args, **kwargs)
            except Exception as exc:
                recorder.record(
                    StageObservation(
                        run_id=run_id,
                        stage=stage,
                        status="FAILED",
                        duration_ms=round((monotonic() - started) * 1000),
                        error_type=type(exc).__name__,
                    )
                )
                raise
            digest = hashlib.sha256(repr(result).encode()).hexdigest()
            recorder.record(
                StageObservation(
                    run_id=run_id,
                    stage=stage,
                    status="COMPLETED",
                    duration_ms=round((monotonic() - started) * 1000),
                    result_sha256=digest,
                )
            )
            return result

        return wrapped

    return decorate
