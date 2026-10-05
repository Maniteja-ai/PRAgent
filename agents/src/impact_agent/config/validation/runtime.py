"""Validation for ``runtime.json``."""

from pydantic import Field, model_validator

from impact_agent.config.validation.common import StrictSettings


class RuntimeConfig(StrictSettings):
    max_calls_per_tool: int = Field(default=5, ge=1, le=100)
    max_total_calls: int = Field(default=30, ge=1, le=500)
    max_reasoning_rounds: int = Field(default=10, ge=1, le=100)
    retry_attempts: int = Field(default=2, ge=0, le=10)
    request_timeout_seconds: float = Field(default=45, gt=0, le=300)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_lease_seconds: int = Field(default=300, ge=10, le=3600)
    job_poll_seconds: float = Field(default=2, gt=0, le=60)

    @model_validator(mode="after")
    def total_calls_covers_one_tool_budget(self) -> "RuntimeConfig":
        if self.max_total_calls < self.max_calls_per_tool:
            raise ValueError("max_total_calls must be at least max_calls_per_tool")
        return self
