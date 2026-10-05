from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StageObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    stage: str
    status: Literal["COMPLETED", "FAILED"]
    duration_ms: int = Field(ge=0)
    result_sha256: str | None = None
    error_type: str | None = None
