"""Fault injection at real adapter boundaries; no external service calls."""

import json
from types import SimpleNamespace

import httpx
import pytest

from tests.ingestion.test_library import DeterministicTestEmbeddings
from tests.ingestion.test_providers import mock_google, project_file
from tests.support.graph import CASES, FIXTURE
from trace_impact import Settings, create_pipeline
from trace_impact.ingestion.storage.qdrant_store import QdrantVectorStore
from trace_impact.retrieval.graph.neo4j_reader import Neo4jGraphReader
from trace_impact.retrieval.graph.service import ImpactRetriever
from trace_impact.shared.errors import ProviderError


@pytest.mark.parametrize("partial", [False, True], ids=["F01-connection", "F02-partial-rows"])
def test_graph_timeout_never_returns_success_or_partial_evidence(partial):
    node = next(n for n in FIXTURE.nodes if n.id == "discount")
    row = {"node": {**node.model_dump(), "properties_json": json.dumps(node.properties)}}

    def rows():
        yield row
        raise TimeoutError("Synthetic failure after first row")

    def execute(*args, **kwargs):
        if partial:
            return rows(), None, None
        raise TimeoutError("Synthetic connection timeout")

    graph = Neo4jGraphReader(SimpleNamespace(execute_query=execute), "neo4j", "fixture")
    # The adapter is atomic: consumed rows are discarded on error. F02's proposed
    # PARTIAL_RESULT status is not implemented; this verifies its safety invariant.
    with pytest.raises(TimeoutError):
        ImpactRetriever(graph).retrieve(CASES[1]["input"])


def test_F03_qdrant_outage_is_not_empty_success():
    def unavailable(*args, **kwargs):
        raise ConnectionError("Synthetic Qdrant outage")

    store = QdrantVectorStore(SimpleNamespace(query_points=unavailable))
    with pytest.raises(ConnectionError):
        store.search(DeterministicTestEmbeddings.profile, "p", "v", [1.0, 0.0, 0.0], 5)


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_F04_embedding_429_is_bounded_and_preserves_incomplete_index(tmp_path, monkeypatch, retries):
    calls = []

    def quota(request):
        calls.append(request)
        return httpx.Response(
            429,
            headers={"Retry-After": "0"},
            json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Test quota"}},
        )

    clients, _ = mock_google(monkeypatch, quota)
    with create_pipeline(
        Settings(gemini_api_key="test-only", model_retries=retries, qdrant_path=str(tmp_path / "vectors"))
    ) as app:
        run, _ = app.collect(project_file(tmp_path), tmp_path / "runs")
        with pytest.raises(ProviderError):
            app.index(run)
        assert json.loads((run / "vector-index.json").read_text())["status"] == "PARTIAL"
        assert not list((run / "embedding-cache").glob("*.json"))
    assert len(calls) == retries + 1
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("vector", [[1.0], [0.0, 0.0, 0.0], [float("nan"), 0.0, 1.0]])
def test_F05_invalid_query_vector_never_reaches_database(vector):
    store = QdrantVectorStore(SimpleNamespace(query_points=lambda *a, **k: pytest.fail("Database called")))
    with pytest.raises(ValueError, match="profile dimensions"):
        store.search(DeterministicTestEmbeddings.profile, "p", "v", vector, 5)


@pytest.mark.parametrize("field", ["project_id", "run_id", "profile_id"])
def test_F07_backend_filter_violation_fails_before_scope_is_discarded(field):
    profile = DeterministicTestEmbeddings.profile
    payload = {"project_id": "p", "run_id": "v", "profile_id": profile.id}
    payload[field] = "outside-scope"
    store = QdrantVectorStore(
        SimpleNamespace(
            query_points=lambda *a, **k: SimpleNamespace(points=[SimpleNamespace(payload=payload)])
        )
    )
    with pytest.raises(ValueError, match="outside the requested scope"):
        store.search(profile, "p", "v", [1.0, 0.0, 0.0], 5)
