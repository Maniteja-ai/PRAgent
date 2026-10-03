"""Stable tool contract used by the coordinator runtime."""

from typing import Protocol

from pydantic import BaseModel

from trace_coordinator.domain.models import ToolContext, ToolResult


class Tool(Protocol):
    name: str
    description: str
    version: str
    allowed_agents: frozenset[str]

    @property
    def input_model(self) -> type[BaseModel]: ...

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolResult: ...
