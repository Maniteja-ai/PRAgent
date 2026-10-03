"""Strict JSON options, descriptive schemas, and configuration-relative paths."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from trace_coordinator.artifact_security import ArtifactSecurityConfig
from trace_coordinator.guardrails import GuardrailPolicy
from trace_coordinator.models import Record


class CallLimits(Record):
    per_agent_tool: int = Field(
        default=5,
        ge=1,
        le=5,
        description="Maximum executed attempts per run, stable agent identity and canonical tool. Includes failures and retries; resume never resets it.",
    )
    overrides: dict[str, dict[str, Annotated[StrictInt, Field(ge=1, le=5)]]] = Field(
        default_factory=dict,
        description="Optional lower limits: agent ID -> canonical tool ID -> limit (1 to default).",
    )
    total_calls: int = Field(
        default=30, ge=1, le=1000, description="Combined tool and model attempts per run."
    )
    max_rounds: int = Field(default=10, ge=1, le=100)
    max_review_requests: int = Field(default=2, ge=0, le=10)
    max_validation_repairs: int = Field(
        default=1,
        ge=0,
        le=2,
        description="Bounded citation correction turns; each consumes model.decide and round budgets.",
    )
    max_run_seconds: int = Field(default=900, ge=1, le=86400)
    retry_attempts: int = Field(default=2, ge=1, le=5, description="Total attempts, including the first.")
    retry_delay_seconds: float = Field(default=1, ge=0, le=30)

    @model_validator(mode="after")
    def lower_overrides(self):
        if any(
            not 1 <= value <= self.per_agent_tool
            for values in self.overrides.values()
            for value in values.values()
        ):
            raise ValueError("Overrides may only lower the per-agent/tool limit")
        return self

    def limit_for(self, agent: str, tool: str) -> int:
        return self.overrides.get(agent, {}).get(tool, self.per_agent_tool)


class FixtureProvider(Record):
    provider: Literal["fixture"] = "fixture"
    file: str = Field(min_length=1, description="Replay fixture, relative to this configuration file.")


class GeminiProvider(Record):
    provider: Literal["gemini"]
    model: str = Field(min_length=1)
    api_key_env: str = Field(default="COORDINATOR_LLM_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout_seconds: float = Field(default=45, gt=0, le=120)
    max_output_tokens: int = Field(default=3000, ge=256, le=16000)
    max_input_chars: int = Field(default=120000, ge=1000, le=500000)
    retry_invalid_response: bool = Field(
        default=False,
        description="Allow the shared dispatcher to retry invalid structured output within retry_attempts and the same model.decide budget. No invalid decision is executed.",
    )


class OpenAIProvider(GeminiProvider):
    provider: Literal["openai"]


class LiveProvider(Record):
    provider: Literal["live"]
    application_file: str = Field(min_length=1)


class ExplorationConfig(Record):
    enabled: bool = False
    environment: Literal["baseline", "patched"] = "patched"
    goal: str = Field(
        default="Explore the UI affected by the change using observed controls.",
        min_length=1,
        max_length=2000,
    )
    target_controls: tuple[str, ...] = Field(
        default=(),
        max_length=20,
        description="Stop discovery when any exact accessible control name is observed. Observation is not a behavior check.",
    )
    max_steps: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Maximum proposed exploration actions. Model/tool attempt limits still apply; one model attempt is reserved for reporting.",
    )
    max_state_visits: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Stop when the latest meaningful screen fingerprint has been captured this many times.",
    )


class ScenarioBinding(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    description: str = Field(min_length=1, max_length=500)
    config_file: str = Field(
        min_length=1, description="Approved scenario JSON, relative to coordinator JSON."
    )
    changed_paths: tuple[str, ...] = Field(
        min_length=1, max_length=20, description="Repository-relative glob patterns selecting this scenario."
    )


class VerificationPolicy(Record):
    enabled: bool = False
    approval: Literal["human_review", "preapproved"] = Field(
        default="human_review",
        description="Preapproved authorizes only configured scenarios; models cannot introduce new test actions.",
    )
    scenarios: tuple[ScenarioBinding, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def valid_catalog(self):
        ids = [item.id for item in self.scenarios]
        if len(ids) != len(set(ids)) or (self.enabled and not ids):
            raise ValueError("Enabled verification requires a catalog with unique scenario IDs")
        return self


class CoordinatorConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    limits: CallLimits = Field(default_factory=CallLimits)
    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)
    verification: VerificationPolicy = Field(default_factory=VerificationPolicy)
    guardrails: GuardrailPolicy = Field(default_factory=GuardrailPolicy)
    artifact_security: ArtifactSecurityConfig = Field(default_factory=ArtifactSecurityConfig)
    model: FixtureProvider | GeminiProvider | OpenAIProvider = Field(discriminator="provider")
    tools: FixtureProvider | LiveProvider = Field(discriminator="provider")
    env_file: str | None = Field(
        default=None, description="Optional dotenv path relative to this config; never copied to reports."
    )
    state_directory: str = Field(default="../runs", min_length=1)


def load_config(path: Path) -> CoordinatorConfig:
    config = CoordinatorConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    return config


def schema() -> dict:
    return {
        **CoordinatorConfig.model_json_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "PR impact coordinator",
    }
