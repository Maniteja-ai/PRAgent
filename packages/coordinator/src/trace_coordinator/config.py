"""Strict JSON options, descriptive schemas, and configuration-relative paths."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from trace_coordinator.domain.contracts import JsonObject, as_json_object
from trace_coordinator.domain.models import Record
from trace_coordinator.runtime_defaults import (
    default_bool,
    default_float,
    default_int,
    default_limit_overrides,
)
from trace_coordinator.security.artifact_security import ArtifactSecurityConfig
from trace_coordinator.security.guardrails import GuardrailPolicy


class CallLimits(Record):
    per_agent_tool: int = Field(
        default=default_int("calls", "per_tool"),
        ge=1,
        le=5,
        description="Maximum executed attempts per run, stable agent identity and canonical tool. Includes failures and retries; resume never resets it.",
    )
    overrides: dict[str, dict[str, Annotated[StrictInt, Field(ge=1, le=5)]]] = Field(
        default_factory=default_limit_overrides,
        description="Optional lower limits: agent ID -> canonical tool ID -> limit (1 to default).",
    )
    total_calls: int = Field(
        default=default_int("calls", "total"),
        ge=1,
        le=1000,
        description="Combined tool and model attempts per run.",
    )
    max_rounds: int = Field(default=default_int("calls", "reasoning_rounds"), ge=1, le=100)
    max_review_requests: int = Field(default=default_int("calls", "review_requests"), ge=0, le=10)
    max_validation_repairs: int = Field(
        default=default_int("calls", "citation_repairs"),
        ge=0,
        le=2,
        description="Bounded citation correction turns; each consumes model.decide and round budgets.",
    )
    max_run_seconds: int = Field(default=default_int("timeouts", "run_seconds"), ge=1, le=86400)
    retry_attempts: int = Field(
        default=default_int("retries", "attempts"),
        ge=1,
        le=5,
        description="Total attempts, including the first.",
    )
    retry_delay_seconds: float = Field(default=default_float("retries", "delay_seconds"), ge=0, le=30)

    @model_validator(mode="after")
    def lower_overrides(self) -> Self:
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


class ModelProvider(Record):
    model: str = Field(min_length=1)
    api_key_env: str = Field(default="COORDINATOR_LLM_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout_seconds: float = Field(default=default_float("timeouts", "model_seconds"), gt=0, le=120)
    max_output_tokens: int = Field(default=3000, ge=256, le=16000)
    max_input_chars: int = Field(default=120000, ge=1000, le=500000)
    retry_invalid_response: bool = Field(
        default=default_bool("retries", "invalid_model_response"),
        description="Allow the shared dispatcher to retry invalid structured output within retry_attempts and the same model.decide budget. No invalid decision is executed.",
    )


class GeminiProvider(ModelProvider):
    provider: Literal["gemini"]


class OpenAIProvider(ModelProvider):
    provider: Literal["openai"]


class LiveToolProvider(Record):
    provider: Literal["live"]
    application_config_file: str = Field(
        min_length=1,
        description="Application configuration file relative to this coordinator configuration.",
    )


class UIExplorationConfig(Record):
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
        description="Maximum proposed UI exploration actions. Model/tool attempt limits still apply; one model attempt is reserved for reporting.",
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
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    enabled: bool = False
    approval: Literal["human_review", "preapproved"] = Field(
        default="human_review",
        description="Preapproved authorizes only configured scenarios; models cannot introduce new test actions.",
    )
    scenarios: tuple[ScenarioBinding, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def valid_catalog(self) -> Self:
        ids = [item.id for item in self.scenarios]
        if len(ids) != len(set(ids)) or (self.enabled and not ids):
            raise ValueError("Enabled verification requires a catalog with unique scenario IDs")
        return self


class HumanReviewPolicy(Record):
    policy: Literal["blocking", "non_blocking"] = Field(
        default="blocking",
        description=(
            "blocking checkpoints the graph until an answer arrives; non_blocking finalizes with "
            "explicit gaps and permits a linked later verification run."
        ),
    )
    allow_follow_up_verification: bool = Field(
        default=True,
        description=(
            "Allow a later answer to an unanswered verification-approval question to create one "
            "linked run without resetting the original call ledger."
        ),
    )


class DisabledObservability(Record):
    provider: Literal["disabled"] = "disabled"


class LangSmithObservability(Record):
    provider: Literal["langsmith"]
    project: str = Field(
        default="testsigma-impact-agent-demo",
        min_length=1,
        max_length=200,
        description="LangSmith project receiving the coordinator trace.",
    )
    api_key_env: str = Field(
        default="LANGSMITH_API_KEY",
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Environment variable containing the LangSmith API key.",
    )
    endpoint_env: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Optional environment variable containing a self-hosted or regional API URL.",
    )
    workspace_id_env: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Optional workspace ID environment variable for multi-workspace API keys.",
    )
    dashboard_url: str = Field(
        default="https://smith.langchain.com",
        pattern=r"^https://[^\s]+$",
        max_length=500,
        description="Safe user-facing dashboard link written to reports; no credential is appended.",
    )
    capture_content: Literal[False] = Field(
        default=False,
        description=(
            "Coordinator traces always hide graph inputs and outputs so code, retrieved text, DOM, "
            "review answers and credentials are not uploaded."
        ),
    )
    sampling_rate: float = Field(default=1.0, gt=0, le=1)
    flush_timeout_seconds: float = Field(
        default=2.0,
        ge=0,
        le=10,
        description="Best-effort shutdown wait; tracing failures never change workflow status.",
    )
    request_timeout_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=5,
        description="Short provider connect/read timeout so telemetry cannot materially delay analysis.",
    )


class CallPolicy(Record):
    per_tool: int = Field(default=default_int("calls", "per_tool"), ge=1, le=5)
    total: int = Field(default=default_int("calls", "total"), ge=1, le=1000)
    reasoning_rounds: int = Field(default=default_int("calls", "reasoning_rounds"), ge=1, le=100)
    review_requests: int = Field(default=default_int("calls", "review_requests"), ge=0, le=10)
    citation_repairs: int = Field(default=default_int("calls", "citation_repairs"), ge=0, le=2)
    overrides: dict[str, dict[str, Annotated[StrictInt, Field(ge=1, le=5)]]] = Field(
        default_factory=default_limit_overrides
    )


class RetryPolicy(Record):
    attempts: int = Field(default=default_int("retries", "attempts"), ge=1, le=5)
    delay_seconds: float = Field(default=default_float("retries", "delay_seconds"), ge=0, le=30)
    invalid_model_response: bool = default_bool("retries", "invalid_model_response")


class TimeoutPolicy(Record):
    run_seconds: int = Field(default=default_int("timeouts", "run_seconds"), ge=1, le=86400)
    model_seconds: float = Field(default=default_float("timeouts", "model_seconds"), gt=0, le=120)


class RuntimeConfig(Record):
    """Optional operational overrides; safe defaults apply when omitted."""

    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    calls: CallPolicy = Field(default_factory=CallPolicy)
    retries: RetryPolicy = Field(default_factory=RetryPolicy)
    timeouts: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    guardrails: GuardrailPolicy = Field(default_factory=GuardrailPolicy)
    artifact_security: ArtifactSecurityConfig = Field(default_factory=ArtifactSecurityConfig)
    observability: DisabledObservability | LangSmithObservability = Field(
        default_factory=DisabledObservability,
        discriminator="provider",
    )

    def call_limits(self) -> CallLimits:
        return CallLimits(
            per_agent_tool=self.calls.per_tool,
            overrides=self.calls.overrides,
            total_calls=self.calls.total,
            max_rounds=self.calls.reasoning_rounds,
            max_review_requests=self.calls.review_requests,
            max_validation_repairs=self.calls.citation_repairs,
            max_run_seconds=self.timeouts.run_seconds,
            retry_attempts=self.retries.attempts,
            retry_delay_seconds=self.retries.delay_seconds,
        )


class CoordinatorFile(Record):
    """Small user-facing coordinator file."""

    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    application_config_file: str | None = Field(
        default=None, min_length=1, description="Live application configuration."
    )
    tool_fixture_file: str | None = Field(
        default=None, min_length=1, description="Offline tool fixture used by tests and demos."
    )
    runtime_config_file: str | None = Field(
        default=None,
        min_length=1,
        description="Optional calls, retries, timeouts and safety overrides.",
    )
    ui_exploration: UIExplorationConfig = Field(default_factory=UIExplorationConfig)
    verification_config_file: str | None = Field(
        default=None,
        min_length=1,
        description="Optional approved verification catalog.",
    )
    human_review: HumanReviewPolicy = Field(default_factory=HumanReviewPolicy)
    model: FixtureProvider | GeminiProvider | OpenAIProvider = Field(discriminator="provider")
    env_file: str | None = Field(
        default=None, description="Optional dotenv path relative to this config; never copied to reports."
    )
    state_directory: str = Field(default="../runs", min_length=1)

    @model_validator(mode="after")
    def one_tool_source(self) -> Self:
        if (self.application_config_file is None) == (self.tool_fixture_file is None):
            raise ValueError("Set exactly one of application_config_file or tool_fixture_file")
        return self


class CoordinatorConfig(Record):
    """Fully resolved coordinator configuration used by the composition root."""

    schema_version: Literal[1] = 1
    limits: CallLimits
    ui_exploration: UIExplorationConfig
    verification: VerificationPolicy
    human_review: HumanReviewPolicy
    guardrails: GuardrailPolicy
    artifact_security: ArtifactSecurityConfig
    observability: DisabledObservability | LangSmithObservability = Field(discriminator="provider")
    model: FixtureProvider | GeminiProvider | OpenAIProvider = Field(discriminator="provider")
    tool_provider: FixtureProvider | LiveToolProvider = Field(discriminator="provider")
    env_file: str | None
    state_directory: str


def load_config(path: Path) -> CoordinatorConfig:
    resolved = path.resolve()
    selected = CoordinatorFile.model_validate_json(resolved.read_text(encoding="utf-8-sig"))
    runtime = (
        RuntimeConfig.model_validate_json(
            (resolved.parent / selected.runtime_config_file).resolve().read_text(encoding="utf-8-sig")
        )
        if selected.runtime_config_file
        else RuntimeConfig()
    )
    if selected.verification_config_file:
        verification_path = (resolved.parent / selected.verification_config_file).resolve()
        parsed_verification = VerificationPolicy.model_validate_json(
            verification_path.read_text(encoding="utf-8-sig")
        )
        verification = parsed_verification.model_copy(
            update={
                "scenarios": tuple(
                    scenario.model_copy(
                        update={
                            "config_file": str((verification_path.parent / scenario.config_file).resolve())
                        }
                    )
                    for scenario in parsed_verification.scenarios
                )
            }
        )
    else:
        verification = VerificationPolicy()
    tools: FixtureProvider | LiveToolProvider
    if selected.application_config_file:
        tools = LiveToolProvider(provider="live", application_config_file=selected.application_config_file)
    else:
        tools = FixtureProvider(provider="fixture", file=selected.tool_fixture_file or "")
    model = selected.model
    if model.provider != "fixture":
        model = model.model_copy(
            update={
                "timeout_seconds": runtime.timeouts.model_seconds,
                "retry_invalid_response": runtime.retries.invalid_model_response,
            }
        )
    return CoordinatorConfig(
        limits=runtime.call_limits(),
        ui_exploration=selected.ui_exploration,
        verification=verification,
        human_review=selected.human_review,
        guardrails=runtime.guardrails,
        artifact_security=runtime.artifact_security,
        observability=runtime.observability,
        model=model,
        tool_provider=tools,
        env_file=selected.env_file,
        state_directory=selected.state_directory,
    )


def schema() -> JsonObject:
    document = CoordinatorFile.model_json_schema()
    document["oneOf"] = [
        {
            "required": ["application_config_file"],
            "not": {"required": ["tool_fixture_file"]},
        },
        {
            "required": ["tool_fixture_file"],
            "not": {"required": ["application_config_file"]},
        },
    ]
    return as_json_object(
        {
            **document,
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "PR impact coordinator",
        }
    )


def runtime_schema() -> JsonObject:
    return as_json_object(
        {
            **RuntimeConfig.model_json_schema(),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Coordinator runtime policy",
        }
    )


def verification_policy_schema() -> JsonObject:
    return as_json_object(
        {
            **VerificationPolicy.model_json_schema(),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Verification policy",
        }
    )
