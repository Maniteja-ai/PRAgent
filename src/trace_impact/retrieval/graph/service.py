"""Bounded reverse dependency traversal, followed by evidence-backed UI/flow links."""

from pydantic import ValidationError

from trace_impact.shared.graph_models import ImpactQuery, ImpactResult, Witness


class ImpactRetriever:
    def __init__(self, reader, *, max_nodes=5000, max_edges=20000):
        if not 1 <= max_nodes <= 30000 or not 1 <= max_edges <= 150000:
            raise ValueError("Invalid graph traversal budget")
        self.reader, self.max_nodes, self.max_edges = reader, max_nodes, max_edges

    def retrieve(self, raw_query) -> ImpactResult:
        try:
            query = ImpactQuery.model_validate(raw_query)
        except ValidationError:
            return ImpactResult(status="INVALID_INPUT")
        if not (query.changed_symbol_ids or query.symbol_lookup or query.changed_files):
            return ImpactResult(status="NO_CHANGES")
        seeds = self.reader.lookup(query, self.max_nodes + 1)
        if len(seeds) > self.max_nodes:
            raise ValueError("Graph node budget exceeded")
        if len({n.id for n in seeds}) != len(seeds):
            raise ValueError("Graph reader returned duplicate seeds")
        if any(
            n.kind != "CodeSymbol"
            or n.project_id != query.scope.project_id
            or n.revision != query.scope.revision
            for n in seeds
        ):
            raise ValueError("Graph reader returned an out-of-scope seed")
        if query.symbol_lookup and len(seeds) > 1:
            return ImpactResult(status="AMBIGUOUS_SYMBOL")
        if not seeds or (query.changed_symbol_ids and set(query.changed_symbol_ids) != {n.id for n in seeds}):
            return ImpactResult(status="SYMBOL_NOT_FOUND")
        if query.changed_files and set(query.changed_files) != {n.properties.get("path") for n in seeds}:
            return ImpactResult(status="SYMBOL_NOT_FOUND")
        nodes = {n.id: n for n in seeds}
        paths = {n.id: Witness(target_id=n.id, node_ids=(n.id,), edge_ids=()) for n in seeds}
        seen_edges = {}

        def expand(frontier, kind, incoming, expected_kind):
            if not frontier:
                return set()
            hits = self.reader.neighbors(
                query.scope, tuple(sorted(frontier)), kind, incoming, self.max_edges + 1
            )
            if len(hits) > self.max_edges:
                raise ValueError("Graph edge budget exceeded")
            found = set()
            for hit in hits:
                node, edge = hit.node, hit.edge
                parent, target = (edge.target, edge.source) if incoming else (edge.source, edge.target)
                if (
                    parent not in frontier
                    or target != node.id
                    or edge.type != kind
                    or edge.status != "CONFIRMED"
                    or node.project_id != query.scope.project_id
                    or node.revision != query.scope.revision
                    or node.kind != expected_kind
                ):
                    raise ValueError("Graph reader returned invalid relationship evidence")
                seen_edges[edge.id] = edge
                nodes[node.id] = node
                if len(seen_edges) > self.max_edges or len(nodes) > self.max_nodes:
                    raise ValueError("Graph traversal budget exceeded; no partial result returned")
                if node.id not in paths:
                    before = paths[parent]
                    paths[node.id] = Witness(
                        target_id=node.id,
                        node_ids=(*before.node_ids, node.id),
                        edge_ids=(*before.edge_ids, edge.id),
                    )
                found.add(node.id)
            return found

        symbols, frontier = set(nodes), set(nodes)
        for _ in range(query.scope.max_dependency_hops):
            reached = expand(frontier, "DEPENDS_ON", True, "CodeSymbol")
            frontier = reached - symbols
            symbols |= reached
            if not frontier:
                break
        # Probe one boundary hop solely to report uncertainty, never include it as impact evidence.
        boundary_nodes, boundary_paths = dict(nodes), dict(paths)
        boundary = expand(frontier, "DEPENDS_ON", True, "CodeSymbol") - symbols if frontier else set()
        nodes, paths = boundary_nodes, boundary_paths
        ui = expand(symbols, "RENDERS", False, "UIElement")
        flows = expand(ui, "CONTAINS", True, "UserFlow")
        requirements = expand(flows, "CHECKS", False, "Requirement")
        status = "OK" if ui else ("UNMAPPED_WITHIN_BUDGET" if boundary else "UNMAPPED")
        return ImpactResult(
            status=status,
            symbol_ids=tuple(sorted(symbols)),
            code_files=tuple(
                sorted({nodes[s].properties["path"] for s in symbols if "path" in nodes[s].properties})
            ),
            ui_ids=tuple(sorted(ui)),
            flow_ids=tuple(sorted(flows)),
            requirement_ids=tuple(sorted(requirements)),
            witness_paths=tuple(paths[n] for n in sorted(paths)),
            dependency_budget_reached=bool(boundary),
            evidence_nodes=tuple(nodes[n] for n in sorted(nodes)),
            evidence_edges=tuple(
                seen_edges[e] for e in sorted({e for p in paths.values() for e in p.edge_ids})
            ),
        )
