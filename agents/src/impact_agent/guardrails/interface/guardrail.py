"""Port for checks enforced around untrusted PR and model content."""

from typing import Protocol

from impact_agent.domain.models import Decision, Evidence, PullRequestRef


class Guardrail(Protocol):
    def validate_request(self, reference: PullRequestRef) -> None: ...

    def validate_evidence(self, evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]: ...

    def validate_decision(self, decision: Decision, evidence: tuple[Evidence, ...]) -> Decision: ...

    def validate_output(self, report_text: str) -> str: ...
