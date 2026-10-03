"""Record stage evidence; metric scoring remains in the offline eval package."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, ParamSpec, Protocol, TypeVar

from pydantic import Field

from trace_impact.shared.contracts import Contract

Params = ParamSpec("Params")
ResultT = TypeVar("ResultT")


class StageObservation(Contract):
    run_id: str
    stage: str
    status: str
    started_at: str
    duration_ms: int = Field(ge=0)
    result_sha256: str | None = None
    result_type: str | None = None
    error_type: str | None = None


class StageRecorder(Protocol):
    def record(self, observation: StageObservation) -> None: ...


class NullStageRecorder:
    def record(self, observation: StageObservation) -> None:
        del observation


class JsonlStageRecorder:
    """Append-only recorder safe for concurrent stages in one process."""

    def __init__(self, directory: Path):
        self._path = directory / "stage-observations.jsonl"
        self._lock = Lock()

    def record(self, observation: StageObservation) -> None:
        line = observation.model_dump_json() + "\n"
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(line)


class RecordedStageOwner(Protocol):
    run_id: str
    stage_recorder: StageRecorder


def _fingerprint(value: Any) -> str:
    if hasattr(value, "model_dump_json"):
        encoded = value.model_dump_json().encode()
    else:
        encoded = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def record_stage(stage: str) -> Callable[[Callable[Params, ResultT]], Callable[Params, ResultT]]:
    """Decorate an orchestrator method and record success or failure without changing its result."""

    def decorate(function: Callable[Params, ResultT]) -> Callable[Params, ResultT]:
        @wraps(function)
        def wrapped(*args: Params.args, **kwargs: Params.kwargs) -> ResultT:
            owner = args[0]
            if not hasattr(owner, "stage_recorder") or not hasattr(owner, "run_id"):
                raise TypeError("@record_stage methods require run_id and stage_recorder attributes")
            typed_owner: RecordedStageOwner = owner
            started_at = datetime.now(UTC).isoformat()
            started = monotonic()
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                typed_owner.stage_recorder.record(
                    StageObservation(
                        run_id=typed_owner.run_id,
                        stage=stage,
                        status="FAILED",
                        started_at=started_at,
                        duration_ms=round((monotonic() - started) * 1000),
                        error_type=type(exc).__name__,
                    )
                )
                raise
            typed_owner.stage_recorder.record(
                StageObservation(
                    run_id=typed_owner.run_id,
                    stage=stage,
                    status="COMPLETED",
                    started_at=started_at,
                    duration_ms=round((monotonic() - started) * 1000),
                    result_sha256=_fingerprint(result),
                    result_type=type(result).__name__,
                )
            )
            return result

        return wrapped

    return decorate
