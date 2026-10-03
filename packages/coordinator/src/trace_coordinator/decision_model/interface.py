"""Stable decision-model contract used by the coordinator."""

from collections.abc import Mapping
from typing import Protocol

from trace_coordinator.domain.models import Decision


class DecisionModel(Protocol):
    version: str

    def decide(self, context: Mapping[str, object]) -> Decision: ...
