"""Frozen graph fixtures and a deterministic test reader."""

import json
from pathlib import Path

from trace_impact.shared.graph_models import GraphSnapshot, Neighbor

ROOT = Path(__file__).resolve().parents[2]


DATA = ROOT / "evaluation/datasets/saleor-retrieval-v0.1"


FIXTURE = GraphSnapshot.model_validate_json((DATA / "inputs/graph/fixture.json").read_text())


CASES = [json.loads(line) for line in (DATA / "labels/graph-cases.jsonl").read_text().splitlines()]


class MemoryReader:
    def __init__(self, snapshot=FIXTURE):
        self.nodes = {n.id: n for n in snapshot.nodes}
        self.edges = snapshot.edges

    def scoped(self, node, scope):
        return node.project_id == scope.project_id and node.revision == scope.revision

    def lookup(self, query, limit):
        return tuple(
            n
            for n in self.nodes.values()
            if self.scoped(n, query.scope)
            and n.kind == "CodeSymbol"
            and (
                n.id in query.changed_symbol_ids
                or (query.symbol_lookup and n.name == query.symbol_lookup.name)
                or n.properties.get("path") in query.changed_files
            )
        )[:limit]

    def neighbors(self, scope, ids, kind, incoming, limit):
        hits = []
        for edge in self.edges:
            a, b = self.nodes[edge.source], self.nodes[edge.target]
            parent, target = (b, a) if incoming else (a, b)
            if (
                parent.id in ids
                and edge.type == kind
                and edge.status in scope.mapping_statuses
                and self.scoped(a, scope)
                and self.scoped(b, scope)
            ):
                hits.append(Neighbor(node=target, edge=edge))
        return tuple(sorted(hits, key=lambda h: (h.node.id, h.edge.id)))[:limit]
