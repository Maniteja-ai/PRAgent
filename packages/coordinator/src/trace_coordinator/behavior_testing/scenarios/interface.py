"""Contract for approved, bounded verification scenarios."""

from typing import Protocol

from trace_coordinator.analysis_workflow.execution import ToolRuntime
from trace_coordinator.domain.contracts import VerificationResultPayload
from trace_coordinator.domain.models import ToolContext


class BehaviorScenario(Protocol):
    id: str
    version: str
    description: str
    changed_paths: tuple[str, ...]
    required_calls: dict[str, int]

    def execute(self, runtime: ToolRuntime, context: ToolContext) -> VerificationResultPayload: ...
