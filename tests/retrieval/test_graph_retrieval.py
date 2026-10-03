"""Scoped graph retrieval contracts and failure handling."""

import pytest
from pydantic import ValidationError

from tests.support.graph import CASES, FIXTURE, MemoryReader
from trace_impact.retrieval.graph.service import ImpactRetriever
from trace_impact.shared.graph_models import GraphSnapshot


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_frozen_contract(case):
    result = ImpactRetriever(MemoryReader()).retrieve(case["input"])
    assert result.status == case["reference"]["status"]
    for field in ("ui_ids", "flow_ids", "requirement_ids"):
        assert set(getattr(result, field)) == set(case["reference"][field])
    assert not set(result.ui_ids + result.flow_ids + result.requirement_ids) & set(
        case["reference"]["forbidden_ids"]
    )
    for path in result.witness_paths:
        assert len(path.node_ids) == len(path.edge_ids) + 1
        assert len(set(path.node_ids)) == len(path.node_ids)


def test_traversal_budgets_fail_instead_of_partial_results():
    query = CASES[1]["input"]
    with pytest.raises(ValueError, match="budget"):
        ImpactRetriever(MemoryReader(), max_nodes=1).retrieve(query)
    with pytest.raises(ValueError, match="budget"):
        ImpactRetriever(MemoryReader(), max_edges=1).retrieve(query)
    with pytest.raises(ValueError, match="budget"):
        ImpactRetriever(MemoryReader(), max_nodes=0)


def test_reader_failures_and_scope_leaks_are_not_empty_success():
    class Broken(MemoryReader):
        def neighbors(self, *args):
            raise ConnectionError("offline")

    with pytest.raises(ConnectionError):
        ImpactRetriever(Broken()).retrieve(CASES[1]["input"])

    class Leaky(MemoryReader):
        def lookup(self, query, limit):
            return (self.nodes["discount"].model_copy(update={"project_id": "elsewhere"}),)

    with pytest.raises(ValueError, match="scope"):
        ImpactRetriever(Leaky()).retrieve(CASES[1]["input"])

    class BadEdge(MemoryReader):
        def neighbors(self, *args):
            hit = super().neighbors(*args)[0]
            return (hit.model_copy(update={"edge": hit.edge.model_copy(update={"status": "CANDIDATE"})}),)

    with pytest.raises(ValueError, match="invalid relationship"):
        ImpactRetriever(BadEdge()).retrieve(CASES[1]["input"])


def test_ambiguous_query_and_bad_graph_fail_validation():
    query = {**CASES[0]["input"], "symbol_lookup": {"name": "summary"}}
    assert ImpactRetriever(MemoryReader()).retrieve(query).status == "INVALID_INPUT"
    for mutation in (
        {"nodes": [FIXTURE.nodes[0], FIXTURE.nodes[0]]},
        {"edges": [FIXTURE.edges[0].model_copy(update={"target": "missing"})]},
        {"edges": [FIXTURE.edges[0].model_copy(update={"type": "RENDERS"})]},
    ):
        with pytest.raises(ValidationError):
            GraphSnapshot.model_validate({**FIXTURE.model_dump(), **mutation})
