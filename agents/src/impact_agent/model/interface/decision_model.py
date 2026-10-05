"""Port for producing evidence-grounded impact findings."""

from typing import Protocol

from impact_agent.domain.models import Decision, Evidence, PullRequestSnapshot


class DecisionModel(Protocol):
    def decide(
        self, pull_request: PullRequestSnapshot, evidence: tuple[Evidence, ...]
    ) -> Decision: ...
