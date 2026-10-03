"""Interfaces."""

from typing import Protocol

from trace_impact.shared.graph_models import GraphNode, GraphScope, ImpactQuery, Neighbor


class GraphReader(Protocol):
    def lookup(self, query: ImpactQuery, limit: int) -> tuple[GraphNode, ...]: ...
    def neighbors(
        self, scope: GraphScope, ids: tuple[str, ...], kind: str, incoming: bool, limit: int
    ) -> tuple[Neighbor, ...]: ...
