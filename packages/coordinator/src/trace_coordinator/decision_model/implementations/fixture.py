"""Deterministic decision-model replay for tests and offline demonstrations."""

from collections.abc import Mapping, Sequence

from trace_coordinator.decision_model.interface import DecisionModel
from trace_coordinator.domain.models import Decision
from trace_coordinator.infrastructure.ledger import digest


class FixtureDecisionModel(DecisionModel):
    def __init__(self, decisions: Sequence[object]) -> None:
        self.decisions = tuple(Decision.model_validate(item) for item in decisions)
        if not self.decisions:
            raise ValueError("A replay requires at least one decision")
        self.version = "fixture-v1:" + digest(decisions)

    def decide(self, context: Mapping[str, object]) -> Decision:
        round_number = context.get("round")
        if not isinstance(round_number, int):
            raise ValueError("Fixture model context requires an integer round")
        return self.decisions[min(round_number - 1, len(self.decisions) - 1)]
