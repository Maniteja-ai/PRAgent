"""Stable impact tags are the deterministic bridge between source and live UI."""

import hashlib

import pytest
from pydantic import ValidationError

from trace_impact.ingestion.mappings import (
    ObservedImpactElement,
    StableImpactTagMapper,
    UiObservation,
)
from trace_impact.shared.graph_models import GraphNode, GraphSnapshot


def code_graph(revision: str = "a" * 40) -> GraphSnapshot:
    node = GraphNode(
        id="src/summary.tsx#Summary",
        kind="CodeSymbol",
        name="Summary",
        project_id="saleor",
        revision=revision,
        properties={
            "path": "src/summary.tsx",
            "sha256": hashlib.sha256(b"source").hexdigest(),
            "impact_ids": ["checkout.discount.apply", "checkout.order-total"],
        },
    )
    return GraphSnapshot(id="code-graph", origin="test", nodes=(node,), edges=())


def observation(*ids: str, revision: str = "a" * 40) -> UiObservation:
    return UiObservation(
        project_id="saleor",
        revision=revision,
        url="https://store.example/checkout",
        reference="artifacts/checkout.observation.json",
        sha256=hashlib.sha256(b"observation").hexdigest(),
        runtime_build_attested=True,
        elements=tuple(ObservedImpactElement(impact_id=value, tag="button", name=value) for value in ids),
    )


def test_exact_static_and_runtime_tags_create_confirmed_graph_edges():
    result = StableImpactTagMapper().map(
        run_id="run-1",
        project_id="saleor",
        code=code_graph(),
        observation=observation("checkout.discount.apply", "checkout.order-total"),
    )
    assert len(result.mappings.candidates) == 2
    assert all(candidate.status == "CONFIRMED" for candidate in result.mappings.candidates)
    assert all(
        candidate.reviewed_by == "stable-impact-tag-validator-v1" for candidate in result.mappings.candidates
    )
    assert {edge.type for edge in result.graph.edges} == {"RENDERS"}
    assert all(edge.properties["runtime_attribution_verified"] for edge in result.graph.edges)
    assert not result.mappings.coverage_gaps


def test_missing_unknown_and_ambiguous_tags_remain_coverage_gaps():
    result = StableImpactTagMapper().map(
        run_id="run-1",
        project_id="saleor",
        code=code_graph(),
        observation=observation(
            "checkout.discount.apply",
            "checkout.discount.apply",
            "checkout.unknown",
        ),
    )
    assert not result.mappings.candidates
    assert len(result.mappings.coverage_gaps) == 3
    assert not result.graph.edges


@pytest.mark.parametrize(
    "changed",
    [
        {"project_id": "other"},
        {"revision": "b" * 40},
    ],
)
def test_scope_mismatch_is_rejected(changed):
    observed = observation().model_copy(update=changed)
    with pytest.raises(ValueError, match="project|revision"):
        StableImpactTagMapper().map(
            run_id="run-1", project_id="saleor", code=code_graph(), observation=observed
        )


def test_unattested_runtime_cannot_create_confirmed_mapping():
    payload = observation("checkout.discount.apply").model_dump()
    payload["runtime_build_attested"] = False
    with pytest.raises(ValidationError, match="runtime_build_attested"):
        UiObservation.model_validate(payload)
