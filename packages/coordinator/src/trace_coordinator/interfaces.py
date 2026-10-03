"""Implement these contracts to add tools or models without changing the graph."""

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel

from trace_coordinator.models import Decision, ToolContext, ToolResult


class Tool(Protocol):
    name: str
    description: str
    version: str
    input_model: type[BaseModel]
    allowed_agents: frozenset[str]

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolResult: ...


class DecisionModel(Protocol):
    version: str

    def decide(self, context: Mapping) -> Decision: ...
