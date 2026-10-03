"""Only serializable evidence and progress belong in graph state."""

from typing import TypedDict, cast

from trace_coordinator.domain.contracts import (
    AnalysisReportPayload,
    AnalysisRequestPayload,
    CallUsagePayload,
    DecisionPayload,
    EvidencePayload,
    FindingPayload,
    ReviewRequestPayload,
    VerificationPlanPayload,
    VerificationResultPayload,
)
from trace_coordinator.domain.models import Evidence


class AnalysisState(TypedDict, total=False):
    request: AnalysisRequestPayload
    evidence: dict[str, EvidencePayload]
    gaps: list[str]
    rounds: int
    review_count: int
    reviews: list[str]
    review_requests: list[ReviewRequestPayload]
    review_outcome: str
    decision: DecisionPayload
    findings: list[FindingPayload]
    stop_reason: str
    report: AnalysisReportPayload
    usage: list[CallUsagePayload]
    validation_repairs: int
    validation_errors: list[str]
    phase: str
    ui_exploration_steps: int
    ui_exploration_status: str
    verification_plan: VerificationPlanPayload
    verification_result: VerificationResultPayload
    verification_approved: bool


def merge_evidence(
    existing: dict[str, EvidencePayload], incoming: tuple[Evidence, ...] | list[Evidence]
) -> dict[str, EvidencePayload]:
    merged = dict(existing)
    for evidence in incoming:
        value = cast(EvidencePayload, evidence.model_dump(mode="json"))
        if evidence.id in merged and merged[evidence.id] != value:
            raise ValueError("Conflicting evidence identity")
        merged[evidence.id] = value
    return merged
