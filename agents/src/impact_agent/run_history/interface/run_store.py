"""Port for saving and loading completed agent runs."""

from typing import Protocol

from impact_agent.domain.models import AgentReport


class RunStore(Protocol):
    def save(self, report: AgentReport) -> None: ...

    def load(self, run_id: str) -> AgentReport | None: ...
