"""Provider-independent graph contracts. Local IDs are scoped by immutable snapshot."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from trace_impact.shared.contracts import Contract


class GraphNode(Contract):
    id: str = Field(min_length=1)
    kind: Literal["CodeFile", "CodeSymbol", "UIElement", "UserFlow", "Requirement"]
    name: str
    project_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class GraphEdge(Contract):
    id: str = Field(min_length=1)
    source: str
    target: str
    type: Literal["DEPENDS_ON", "RENDERS", "CONTAINS", "CHECKS", "DECLARES", "IMPORTS"]
    status: Literal["CONFIRMED", "CANDIDATE", "REJECTED"] = "CONFIRMED"
    properties: dict[str, JsonValue] = Field(default_factory=dict)


class GraphSnapshot(Contract):
    id: str = Field(min_length=1)
    origin: str
    nodes: tuple[GraphNode, ...] = Field(max_length=30000)
    edges: tuple[GraphEdge, ...] = Field(max_length=150000)
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_references(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes) or len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("Graph identities must be unique within a snapshot")
        if any(e.source not in ids or e.target not in ids for e in self.edges):
            raise ValueError("Every graph edge must reference existing nodes")
        kinds = {n.id: n.kind for n in self.nodes}
        allowed = {
            "DEPENDS_ON": ("CodeSymbol", "CodeSymbol"),
            "IMPORTS": ("CodeFile", "CodeFile"),
            "DECLARES": ("CodeFile", "CodeSymbol"),
            "RENDERS": ("CodeSymbol", "UIElement"),
            "CONTAINS": ("UserFlow", "UIElement"),
            "CHECKS": ("UserFlow", "Requirement"),
        }
        if any((kinds[e.source], kinds[e.target]) != allowed[e.type] for e in self.edges):
            raise ValueError("Graph edge kinds do not match their declared relationship")
        return self


class GraphScope(Contract):
    project_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    max_dependency_hops: int = Field(default=3, ge=0, le=8)
    mapping_statuses: tuple[Literal["CONFIRMED"], ...] = Field(
        default=("CONFIRMED",), min_length=1, max_length=1
    )


class SymbolLookup(Contract):
    name: str = Field(min_length=1)


class ImpactQuery(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    id: str = ""
    fixture_id: str | None = None
    operation: Literal["impact_neighborhood"] = "impact_neighborhood"
    changed_symbol_ids: tuple[str, ...] = Field(default=(), max_length=1000)
    changed_files: tuple[str, ...] = Field(default=(), max_length=100)
    symbol_lookup: SymbolLookup | None = None
    scope: GraphScope

    @model_validator(mode="after")
    def one_lookup(self):
        if sum(bool(v) for v in (self.changed_symbol_ids, self.changed_files, self.symbol_lookup)) > 1:
            raise ValueError("Choose changed IDs, files, or name lookup")
        return self


class Witness(Contract):
    target_id: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]


class ImpactResult(Contract):
    status: Literal[
        "OK",
        "NO_CHANGES",
        "SYMBOL_NOT_FOUND",
        "AMBIGUOUS_SYMBOL",
        "UNMAPPED",
        "UNMAPPED_WITHIN_BUDGET",
        "INVALID_INPUT",
    ]
    symbol_ids: tuple[str, ...] = ()
    code_files: tuple[str, ...] = ()
    ui_ids: tuple[str, ...] = ()
    flow_ids: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()
    witness_paths: tuple[Witness, ...] = ()
    evidence_nodes: tuple[GraphNode, ...] = ()
    evidence_edges: tuple[GraphEdge, ...] = ()
    dependency_budget_reached: bool = False
    result_semantics: Literal["reachable_risk_candidates_not_confirmed_breakage"] = (
        "reachable_risk_candidates_not_confirmed_breakage"
    )
    mapping_coverage: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"


class Neighbor(Contract):
    node: GraphNode
    edge: GraphEdge
