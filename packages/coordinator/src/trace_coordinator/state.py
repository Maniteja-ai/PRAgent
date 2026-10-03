"""Only serializable evidence and progress belong in graph state."""

from typing import TypedDict


class AnalysisState(TypedDict, total=False):
    request: dict
    evidence: dict[str, dict]
    gaps: list[str]
    rounds: int
    review_count: int
    reviews: list[str]
    decision: dict
    findings: list[dict]
    stop_reason: str
    report: dict
    usage: list[dict]
    validation_repairs: int
    validation_errors: list[str]
    phase: str
    exploration_steps: int
    exploration_status: str
    verification_plan: dict
    verification_result: dict
    verification_approved: bool


def merge_evidence(existing: dict, incoming) -> dict:
    merged = dict(existing)
    for evidence in incoming:
        value = evidence.model_dump(mode="json")
        if evidence.id in merged and merged[evidence.id] != value:
            raise ValueError("Conflicting evidence identity")
        merged[evidence.id] = value
    return merged
