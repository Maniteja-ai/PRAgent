"""Port for running explicitly configured behavior checks."""

from typing import Protocol

from impact_agent.domain.models import (
    BehaviorVerification,
    Evidence,
    Finding,
    PullRequestSnapshot,
)


class BehaviorVerifier(Protocol):
    def verify(
        self,
        pull_request: PullRequestSnapshot,
        findings: tuple[Finding, ...],
        evidence: tuple[Evidence, ...],
    ) -> BehaviorVerification: ...
