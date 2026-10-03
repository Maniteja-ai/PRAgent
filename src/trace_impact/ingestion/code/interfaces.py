"""Interfaces."""

from typing import Protocol

from trace_impact.shared.graph_models import GraphSnapshot


class CodeAnalyzer(Protocol):
    def analyze(self, config) -> GraphSnapshot: ...
