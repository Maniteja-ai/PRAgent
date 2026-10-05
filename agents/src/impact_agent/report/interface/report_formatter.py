"""Port for turning a typed analysis report into user-facing output."""

from typing import Protocol

from impact_agent.domain.models import AgentReport


class ReportFormatter(Protocol):
    def format(self, report: AgentReport) -> str: ...
