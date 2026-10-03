"""Implement these contracts to add tools or models without changing the graph."""

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel

from trace_coordinator.domain.contracts import AnalysisReportPayload
from trace_coordinator.domain.models import Decision, ReportNarrative, ToolContext, ToolResult


class Tool(Protocol):
    name: str
    description: str
    version: str
    allowed_agents: frozenset[str]

    @property
    def input_model(self) -> type[BaseModel]: ...

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolResult: ...


class DecisionModel(Protocol):
    version: str

    def decide(self, context: Mapping[str, object]) -> Decision: ...


class ReportWriter(Protocol):
    def write(self, report: AnalysisReportPayload) -> ReportNarrative: ...
