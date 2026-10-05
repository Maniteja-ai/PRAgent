"""One callable LangGraph-compatible pipeline stage."""

from typing import Protocol

from impact_agent.domain.state import AgentState


class PipelineStage(Protocol):
    def __call__(self, state: AgentState) -> AgentState: ...
