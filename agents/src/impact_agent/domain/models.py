"""Small immutable data objects passed between agent components."""

from dataclasses import dataclass
from typing import Literal

from impact_agent.domain.pipeline_enums import CoverageGap, ReportStatus

WebhookRejection = Literal[
    "WEBHOOK_DISABLED",
    "BODY_TOO_LARGE",
    "MISSING_HEADERS",
    "INVALID_SIGNATURE",
    "INVALID_JSON",
    "UNSUPPORTED_EVENT",
    "UNSUPPORTED_ACTION",
    "INVALID_PULL_REQUEST",
]


@dataclass(frozen=True, slots=True)
class PullRequestRef:
    repository: str
    number: int


@dataclass(frozen=True, slots=True)
class WebhookAcknowledgement:
    accepted: bool
    delivery_id: str
    reference: PullRequestRef | None = None
    reason: WebhookRejection | None = None


@dataclass(frozen=True, slots=True)
class WebhookJobResult:
    status: Literal["QUEUED", "DUPLICATE"]


@dataclass(frozen=True, slots=True)
class WebhookJob:
    delivery_id: str
    reference: PullRequestRef
    run_id: str
    attempt: int
    lease_token: str


@dataclass(frozen=True, slots=True)
class WorkerExecution:
    status: Literal["IDLE", "COMPLETED", "RETRY_SCHEDULED", "FAILED"]
    delivery_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    change_type: Literal["added", "modified", "removed", "renamed"]
    additions: int
    deletions: int
    patch: str | None = None


@dataclass(frozen=True, slots=True)
class PullRequestSnapshot:
    reference: PullRequestRef
    title: str
    description: str
    base_sha: str
    head_sha: str
    files: tuple[ChangedFile, ...]
    diff: str


@dataclass(frozen=True, slots=True)
class ConfirmedCodeUiMapping:
    """A graph-confirmed link from a changed code file to a UI route."""

    changed_path: str
    code_path: str
    url: str
    evidence_ids: tuple[str, ...] = ()
    query_parameter_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    source: str
    content: str
    content_sha256: str
    coverage_gap: CoverageGap | None = None
    confirmed_code_ui_mappings: tuple[ConfirmedCodeUiMapping, ...] = ()


@dataclass(frozen=True, slots=True)
class Finding:
    title: str
    explanation: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Decision:
    summary: str
    findings: tuple[Finding, ...]
    open_questions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BehaviorResult:
    scenario_id: str
    status: Literal["PASS", "FAIL", "NOT_RUN", "BLOCKED"]
    summary: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BehaviorVerification:
    """Behavior checks run for one PR and any PR-scoped coverage gaps."""

    results: tuple[BehaviorResult, ...] = ()
    coverage_gaps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentReport:
    run_id: str
    status: ReportStatus
    summary: str
    findings: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]
    behavior_results: tuple[BehaviorResult, ...]
    gaps: tuple[str, ...]
    rendered_report: str = ""


@dataclass(frozen=True, slots=True)
class StageEvaluation:
    stage: str
    status: Literal["COMPLETED", "FAILED"]
    duration_ms: int
    input_sha256: str
    output_sha256: str | None = None
    error_type: str | None = None
