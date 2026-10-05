"""Port for publishing a completed impact report to its pull request."""

from typing import Protocol

from impact_agent.domain.models import AgentReport, PullRequestRef


class ReportCommenter(Protocol):
    def publish(self, reference: PullRequestRef, report: AgentReport) -> None: ...

    def close(self) -> None: ...
