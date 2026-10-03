"""Deterministic offline fixtures, explicitly labelled as replay evidence."""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field

from trace_coordinator.domain.models import Decision, Record, ToolContext, ToolResult
from trace_coordinator.infrastructure.ledger import digest


class PRInput(Record):
    repository: str
    pull_request: int = Field(gt=0)


class QueryInput(Record):
    query: str = Field(min_length=1, max_length=4000)


class ObserveInput(Record):
    goal: str = Field(min_length=1, max_length=4000)


class FixtureTool:
    allowed_agents = frozenset({"coordinator"})

    def __init__(self, name: str, data: object) -> None:
        self.name = name
        self.description = f"Replay saved fixture for {name}; performs no live operation."
        self.input_model = (
            PRInput if name == "github.diff" else (ObserveInput if name == "browser.observe" else QueryInput)
        )
        self.version = "fixture-v1:" + digest(data)
        self.result = ToolResult.model_validate(data)

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolResult:
        return self.result


class FixtureModel:
    def __init__(self, decisions: Sequence[object]) -> None:
        self.decisions = tuple(Decision.model_validate(item) for item in decisions)
        if not self.decisions:
            raise ValueError("A replay requires at least one decision")
        self.version = "fixture-v1:" + digest(decisions)

    def decide(self, context: Mapping[str, object]) -> Decision:
        # Round-based replay remains stable across process restarts.
        round_number = context.get("round")
        if not isinstance(round_number, int):
            raise ValueError("Fixture model context requires an integer round")
        return self.decisions[min(round_number - 1, len(self.decisions) - 1)]
