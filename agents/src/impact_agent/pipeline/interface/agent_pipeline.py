"""Public pipeline contract used by webhook and command-line entry points."""

from typing import Protocol

from impact_agent.domain.models import AgentReport, PullRequestRef


class AgentPipeline(Protocol):
    def run(self, reference: PullRequestRef, run_id: str) -> AgentReport: ...
