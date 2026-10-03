"""Run manifest contracts."""

from __future__ import annotations

from typing import Literal

from trace_impact.shared.contracts import Contract


class StageResult(Contract):
    name: str
    status: Literal["COMPLETED", "SKIPPED", "FAILED"]
    artifact: str | None = None
    detail: str | None = None


class IngestionManifest(Contract):
    schema_version: Literal[1] = 1
    run_id: str
    corpus_run_id: str | None = None
    project_id: str
    status: Literal["COMPLETED", "COMPLETED_WITH_GAPS", "FAILED"]
    config_sha256: str
    stages: tuple[StageResult, ...]
    coverage_gaps: tuple[str, ...] = ()
