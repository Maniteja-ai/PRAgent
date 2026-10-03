"""JSON-safe contracts used between the graph, persistence, and output boundaries.

Domain objects use Pydantic models.  These TypedDicts describe their serialized
form because LangGraph checkpoints and SQLite records must contain plain JSON.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypeAlias, TypedDict

from pydantic import JsonValue, TypeAdapter

JsonObject: TypeAlias = dict[str, JsonValue]
_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_JSON_OBJECT_ADAPTER: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def as_json_value(value: object) -> JsonValue:
    """Validate dynamic adapter data before it enters persisted JSON metadata."""

    return _JSON_VALUE_ADAPTER.validate_python(value)


def as_json_object(value: object) -> JsonObject:
    """Validate and narrow an external mapping to a JSON object."""

    return _JSON_OBJECT_ADAPTER.validate_python(value)


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


class ObservedElementPayload(TypedDict, total=False):
    id: str
    tag: str
    type: str
    name: str
    href: str
    disabled: bool
    checked: bool
    filled: bool
    test_id: str


class ScreenPayload(TypedDict, total=False):
    environment: Literal["baseline", "patched"]
    url: str
    captured_at: float
    text: str
    text_truncated: bool
    elements: list[ObservedElementPayload]
    element_limit_reached: bool
    claimed_revision: str
    runtime_build_attested: bool
    state_fingerprint: str


class FileChangePayload(TypedDict):
    path: str
    status: Literal["A", "D", "M", "T"]


class UICandidateFilePayload(TypedDict):
    path: str
    status: NotRequired[Literal["A", "D", "M", "T"]]


class UICandidateChangePayload(TypedDict):
    analysis_base: str
    analysis_head: str
    files: list[UICandidateFilePayload]


class ChangeSetPayload(TypedDict):
    repository: str
    pull_request: int
    upstream_base: str
    upstream_head: str
    comparison_base: str
    analysis_base: str
    analysis_head: str
    files: list[FileChangePayload]
    patch_sha256: str
    historical_replay: bool
    deployment_patch_equivalent: bool


UITransitionPayload = TypedDict(
    "UITransitionPayload",
    {
        "environment": Literal["baseline", "patched"],
        "from": str | None,
        "to": str,
        "tool": str,
        "path": str,
        "action": str,
        "element_id": str,
        "element_name": str,
    },
    total=False,
)


class UISummaryPayload(TypedDict):
    evidence_id: str
    environment: Literal["baseline", "patched"]
    url: str | None
    state_fingerprint: str | None
    element_count: int


class ObservedPathPayload(TypedDict):
    environment: Literal["baseline", "patched"]
    status: Literal["OBSERVED_PATH"]
    transitions: list[UITransitionPayload]


class UICandidatePayload(TypedDict):
    status: Literal["CANDIDATE"]
    method: Literal["literal_label_in_diff"]
    browser_evidence_id: str
    element_id: str
    label: str
    environment: Literal["baseline", "patched"]
    diff_evidence_id: str
    code_path: str
    line: int
    configured_revision: str
    source_excerpt: str
    runtime_attribution_verified: Literal[False]


class SourceProvenancePayload(TypedDict):
    owner: str
    owner_start_line: int
    source_sha256: str
    path: NotRequired[str]
    line: NotRequired[int]
    wrapper_path: NotRequired[str | None]
    wrapper_sha256: NotRequired[str | None]


class SourceInspectionCandidatePayload(UICandidatePayload):
    patch: str


class ValidatedMappingPayload(TypedDict):
    candidate: UICandidatePayload
    source: SourceProvenancePayload
    observed_element: ObservedElementPayload
    url: str
    flow: ObservedPathPayload
    validation: Literal["STATIC_JSX_AND_OBSERVED_CONTROL"]
    runtime_attribution_verified: Literal[False]
    behavior_verification: Literal["NOT_RUN"]


class CodeNodePayload(TypedDict):
    id: str
    kind: str
    name: str
    project_id: str
    revision: str
    properties: JsonObject


class CodeSnapshotPayload(TypedDict):
    id: str
    origin: str
    diagnostics: list[str]
    edges: list[JsonObject]
    nodes: list[CodeNodePayload]


class UIKnowledgePayload(TypedDict):
    schema_version: Literal[1]
    screens: list[UISummaryPayload]
    discovered_paths: list[ObservedPathPayload]
    code_ui_candidates: list[UICandidatePayload]
    candidates_truncated: bool
    graph_publication: Literal["NOT_PUBLISHED"]
    validation: str


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


class ToolDescriptionPayload(TypedDict):
    name: str
    description: str
    arguments: JsonObject


class UIExplorationContextPayload(TypedDict):
    enabled: bool
    environment: Literal["baseline", "patched"]
    goal: str
    target_controls: list[str]
    max_steps: int
    max_state_visits: int
    status: str
    steps: int


class ModelContextPayload(TypedDict, total=False):
    request: AnalysisRequestPayload
    round: int
    evidence: dict[str, EvidencePayload]
    gaps: list[str]
    human_answers: list[str]
    tools: list[ToolDescriptionPayload]
    per_tool_limit: int
    calls_already_used: list[CallUsagePayload]
    validation_errors: list[str]
    previous_findings: list[FindingPayload]
    phase: str
    ui_exploration: UIExplorationContextPayload
    approved_verification: JsonObject
    latest_snapshot_id: str


VerificationStatus: TypeAlias = Literal[
    "NOT_RUN",
    "NOT_EXECUTED",
    "SELECTED",
    "COMPLETED",
    "BLOCKED",
    "BLOCKED_BUDGET",
    "BLOCKED_UNCERTAIN",
]


class VerificationCheckPayload(TypedDict):
    environment: Literal["baseline", "patched"]
    name: str
    status: Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN"]
    evidence_ids: list[str]


class RequirementCheckPayload(TypedDict):
    id: str
    statement: str
    environment: Literal["baseline", "patched"]
    check_names: list[str]
    status: Literal["PASS", "FAIL", "BLOCKED"]
    evidence_ids: list[str]


class VerificationPlanPayload(TypedDict, total=False):
    status: VerificationStatus
    reason: str
    scenario_id: str
    description: str
    matched_files: list[str]
    finding_titles: list[str]
    required_calls: dict[str, int]
    approval: Literal["human_review", "preapproved"]


class VerificationComparisonPayload(TypedDict):
    status: str
    reason: str


class VerificationResultPayload(TypedDict, total=False):
    schema_version: int
    stage: str
    status: VerificationStatus
    checks: list[VerificationCheckPayload]
    requirements: list[RequirementCheckPayload]
    evidence: dict[str, EvidencePayload]
    tool_usage: list[CallUsagePayload]
    stop_reason: str | None
    comparison: VerificationComparisonPayload
    not_run: list[str]
    llm_calls: int
    reason: str


class ReviewRequestPayload(TypedDict):
    kind: Literal["analysis", "verification_approval"]
    question: str
    status: Literal["NOT_ANSWERED", "ANSWERED", "APPROVED", "REJECTED"]
    answer: str | None
    scenario_id: NotRequired[str]
    scenario: NotRequired[VerificationPlanPayload]


class VerificationApprovalPayload(TypedDict):
    mode: Literal["human_review", "preapproved"]
    approved: bool
    scenario_id: str | None
    policy_fingerprint: str


class HumanReviewPayload(TypedDict):
    policy: Literal["blocking", "non_blocking"]
    status: str
    requests: list[ReviewRequestPayload]
    follow_up_verification_allowed: bool


class RuntimeAttestationPayload(TypedDict):
    status: Literal["VERIFIED", "NOT_VERIFIED"]
    deployments: dict[str, JsonObject]
    shared_backend: bool


class ModelGuardrailsPayload(TypedDict):
    status: Literal["SANITIZED", "PASSED"]
    events: list[AuditEventPayload]
    policy_fingerprint: str


class GuardrailAuditPayload(TypedDict):
    """Result of one deterministic input or output guardrail pass."""

    status: Literal["DISABLED", "BLOCKED", "SANITIZED", "PASSED"]
    counts: dict[str, int]


class UIExplorationReportPayload(TypedDict):
    status: str
    environment: Literal["baseline", "patched"]
    goal: str | None
    steps: int


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
    verification_plan: VerificationPlanPayload
    verification_approval: VerificationApprovalPayload
    human_review: HumanReviewPayload
    behavior_verification: VerificationResultPayload
    runtime_attestation: RuntimeAttestationPayload
    model_guardrails: ModelGuardrailsPayload
    ui_exploration: UIExplorationReportPayload
    ui_knowledge: UIKnowledgePayload
    follow_up: JsonObject
    observability: JsonObject
    limit_events: list[AuditEventPayload]
