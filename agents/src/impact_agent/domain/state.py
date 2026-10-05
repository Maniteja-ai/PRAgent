"""Typed serializable LangGraph state shared between workflow stages."""

from typing import TypedDict

from impact_agent.domain.models import (
    AgentReport,
    BehaviorResult,
    Decision,
    Evidence,
    PullRequestRef,
    PullRequestSnapshot,
)


class AgentState(TypedDict, total=False):
    run_id: str
    request: PullRequestRef
    pull_request: PullRequestSnapshot
    evidence: tuple[Evidence, ...]
    decision: Decision
    report: AgentReport
    behavior_results: tuple[BehaviorResult, ...]
    gaps: tuple[str, ...]
