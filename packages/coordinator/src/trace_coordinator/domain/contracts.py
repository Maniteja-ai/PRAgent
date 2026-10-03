"""JSON-safe contracts used between the graph, persistence, and presentation layers.

Domain objects use Pydantic models.  These TypedDicts describe their serialized
form because LangGraph checkpoints and SQLite records must contain plain JSON.
"""

from typing import Literal, NotRequired, TypeAlias, TypedDict

from pydantic import JsonValue, TypeAdapter

JsonObject: TypeAlias = dict[str, JsonValue]
_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def as_json_value(value: object) -> JsonValue:
    """Validate dynamic adapter data before it enters persisted JSON metadata."""

    return _JSON_VALUE_ADAPTER.validate_python(value)


class AnalysisRequestPayload(TypedDict):
    schema_version: Literal[1]
    project_id: str
    repository: str
    pull_request: int
    question: str


class EvidencePayload(TypedDict):
    id: str
    project_id: str
    kind: Literal["diff", "graph", "document", "browser", "fixture", "verification", "attestation"]
    summary: str
    source: str
    metadata: JsonObject


class FindingPayload(TypedDict):
    title: str
    explanation: str
    evidence_ids: list[str]
    checks: list[str]
    classification: Literal["POTENTIAL_IMPACT"]


class DecisionPayload(TypedDict):
    action: Literal["tool", "review", "finish"]
    tool: str | None
    arguments: JsonObject
    question: str | None
    findings: list[FindingPayload]


class ReviewRequestPayload(TypedDict):
    kind: Literal["analysis", "verification_approval"]
    question: str
    status: Literal["NOT_ANSWERED", "ANSWERED", "APPROVED", "REJECTED"]
    answer: str | None
    scenario_id: NotRequired[str]
    scenario: NotRequired[JsonObject]


class CallUsagePayload(TypedDict):
    agent: str
    tool: str
    attempts: int
    succeeded: int
    failed: int
    uncertain: int


class CallAttemptPayload(TypedDict):
    run: str
    operation: str
    attempt: int
    agent: str
    tool: str
    input_hash: str
    status: str
    result: str | None
    retryable: int
    started: float
    ended: float | None


class AuditEventPayload(TypedDict):
    kind: str
    detail: str
    created: float


class ArtifactPayload(TypedDict):
    path: str
    sha256: str
    bytes: int


class AnalysisReportPayload(TypedDict, total=False):
    schema_version: int
    workflow_version: str
    run_id: str
    parent_run_id: str
    request: AnalysisRequestPayload
    status: str
    completeness: str
    verification: str
    stop_reason: str
    findings: list[FindingPayload]
    evidence: dict[str, EvidencePayload]
    gaps: list[str]
    tool_usage: list[CallUsagePayload]
    audit_events: list[AuditEventPayload]
    verification_plan: JsonObject
    verification_approval: JsonObject
    human_review: JsonObject
    behavior_verification: JsonObject
    runtime_attestation: JsonObject
    model_guardrails: JsonObject
    exploration: JsonObject
    ui_knowledge: JsonObject
    follow_up: JsonObject
    observability: JsonObject
