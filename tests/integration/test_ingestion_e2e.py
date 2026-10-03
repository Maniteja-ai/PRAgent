"""Complete ingestion, without retrieval. Offline contracts and opt-in live services."""

import json
import os
import uuid
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv
from qdrant_client import QdrantClient

from tests.ingestion.test_gemini_interactions import interaction_response
from tests.ingestion.test_providers import extraction_response, mock_google
from trace_impact import Settings, create_pipeline
from trace_impact.ingestion.config import Source, load_project
from trace_impact.ingestion.documents.loaders import GitHubFileLoader, WebLoader
from trace_impact.ingestion.storage.neo4j_requirement_store import Neo4jRequirementStore
from trace_impact.ingestion.storage.qdrant_store import QdrantVectorStore

ROOT = Path(__file__).resolve().parents[2]
QUOTE = "A visitor can search the catalog by book title."
SPEC = "# Synthetic test specification\n\n" + QUOTE


def setup_project(tmp_path, *, live=False):
    project = load_project(ROOT / "configs/ingestion/saleor/project.json")
    project.project_id = "integration-ingestion-" + uuid.uuid4().hex
    project.name = "Synthetic ingestion integration fixture, not Saleor findings"
    project.scope = ["Catalog search by book title"]
    project.extractor.requests_per_minute = 0
    project.embedding_provider.requests_per_minute = 0
    project.sources = [
        Source(
            id="local-spec",
            location="spec.md",
            loader="local_file",
            parser="markdown",
            authority="frontend_spec",
            version="test-v1",
            scope=["search"],
        )
    ]
    if not live:
        project.allowed_document_hosts = ["docs.example.com", "raw.githubusercontent.com"]
        project.sources.extend(
            [
                Source(
                    id="web-spec",
                    location="https://docs.example.com/spec",
                    loader="mock_web",
                    parser="html",
                    authority="frontend_spec",
                    version="test-v1",
                    scope=["search"],
                ),
                Source(
                    id="readme",
                    loader="mock_github",
                    parser="markdown",
                    authority="frontend_spec",
                    version="a" * 40,
                    scope=["search"],
                    options={"repository": "fixture/catalog", "revision": "a" * 40, "path": "README.md"},
                ),
            ]
        )
        project.storage.graph = "recording_graph"
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    path = tmp_path / "project.json"
    path.write_text(project.model_dump_json(), encoding="utf-8")
    return path


class RecordingGraph:
    """Offline boundary double; exercises real Cypher serialization, not a real database."""

    def __init__(self):
        self.calls = []

    def initialize(self):
        pass

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return self

    def consume(self):
        pass

    def load(self, corpus, extraction):
        self.corpus, self.extraction = corpus, extraction
        Neo4jRequirementStore._load_tx(self, corpus, extraction)

    def counts(self, project_id):
        assert self.corpus.project.project_id == project_id
        return {"chunks": len(self.corpus.chunks), "requirements": len(self.extraction.requirements)}


def assert_persisted_vectors(path, profile, corpus):
    # Reopen the real local database: persistence is checked independently of the writer.
    with closing(QdrantClient(path=str(path))) as client:
        points, cursor = client.scroll(QdrantVectorStore.collection(profile), limit=100, with_vectors=True)
        assert cursor is None and len(points) == len(corpus.chunks)
        assert {p.payload["chunk_id"] for p in points} == {c.id for c in corpus.chunks}
        for point in points:
            assert point.payload["project_id"] == corpus.project.project_id
            assert point.payload["run_id"] == corpus.run_id
            assert len(point.vector) == profile.dimensions
            assert point.payload["text"] and point.payload["artifact_path"]


@pytest.mark.parametrize(
    "live_graph",
    [
        False,
        pytest.param(
            True,
            marks=[
                pytest.mark.integration,
                pytest.mark.skipif(
                    os.getenv("RUN_NEO4J_INTEGRATION") != "1", reason="Live Neo4j opt-in required"
                ),
            ],
        ),
    ],
)
def test_ingestion_from_three_source_types_to_both_stores(tmp_path, monkeypatch, live_graph):
    """Real pipeline/Qdrant, fake HTTP; graph transport is recorded or real Aura."""
    calls = []

    def provider_response(request):
        calls.append(str(request.url))
        if "batchEmbedContents" in str(request.url):
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"embeddings": [{"values": [1.0] + [0.0] * 767} for _ in body["requests"]]}
            )
        body = extraction_response().json()
        body["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(
            {
                "requirements": [
                    {
                        "statement": QUOTE,
                        "actor": "visitor",
                        "behavior": "search by title",
                        "preconditions": [],
                        "expected_outcome": "Matching books are returned",
                        "exceptions": [],
                        "layer": "frontend",
                        "support": "documented",
                        "evidence_quote": QUOTE,
                        "uncertainty": [],
                    }
                ],
                "no_requirement_reason": None,
            }
        )
        if request.url.path.endswith("/interactions"):
            return interaction_response(body["candidates"][0]["content"]["parts"][0]["text"])
        return httpx.Response(200, json=body)

    mock_google(monkeypatch, provider_response)
    graph = RecordingGraph()
    vector_path = tmp_path / "qdrant"
    settings = Settings()
    if live_graph:
        load_dotenv(ROOT / ".env", override=False)
        settings = Settings.from_env()
        settings.require_database()
    settings = settings.model_copy(
        update={
            "gemini_api_key": Settings(gemini_api_key="test-only").gemini_api_key,
            "model_retries": 0,
            "qdrant_url": "",
            "qdrant_path": str(vector_path),
        }
    )
    config = setup_project(tmp_path)
    if live_graph:
        project = load_project(config)
        project.storage.graph = "neo4j"
        config.write_text(project.model_dump_json(), encoding="utf-8")

    def source_response(request):
        return httpx.Response(
            200,
            text=(
                f"<main><h1>Search</h1><p>{QUOTE}</p></main>"
                if request.url.host == "docs.example.com"
                else SPEC
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(source_response)) as client:
        with create_pipeline(settings) as app:
            app.components.loaders.register("mock_web", WebLoader(client))
            app.components.loaders.register("mock_github", GitHubFileLoader(WebLoader(client)))
            app.components.graphs.register("recording_graph", graph)
            folder, corpus = app.collect(config, tmp_path / "runs")
            assert not corpus.errors and len(corpus.snapshots) == 3
            result = app.extract(folder)
            assert result.status == "COMPLETE"
            assert len(result.requirements) == 1  # Same requirement, all three citations retained.
            req = result.requirements[0]
            assert req.validation == "GROUNDED_CANDIDATE" and len(req.evidence) == 3
            index = app.index(folder, batch_size=2)
            assert index.status == "COMPLETE"
            counts = app.publish_graph(folder, with_requirements=True)
            assert counts["chunks"] == 3 and counts["requirements"] == 1
            assert app.publish_graph(folder, with_requirements=True) == counts
            calls_before = len(calls)
            assert app.extract(folder).status == "COMPLETE"
            assert app.index(folder).status == "COMPLETE"
            assert len(calls) == calls_before
            assert sum("generateContent" in c or c.endswith("/interactions") for c in calls) == 3
            if live_graph:
                store = app.components.graphs.resolve("neo4j")
                citations, _, _ = store.driver.execute_query(
                    "MATCH (:ExtractionRun {id:$eid})-[:PRODUCED]->(:Requirement)-[e:CITES]->(:ChunkRef) "
                    "RETURN e.quote_verified AS verified",
                    eid=result.id,
                    database_=store.database,
                )
                assert len(citations) == 3 and all(p["verified"] for p in citations)
            else:
                citations = [p for _, p in graph.calls if "verified" in p]
                assert len(citations) == 6 and all(p["verified"] for p in citations)
            assert all(
                (folder / s.raw_file).exists() and (folder / s.text_file).exists() for s in corpus.snapshots
            )
    assert_persisted_vectors(vector_path, index.profile, corpus)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_INGESTION") != "1", reason="Set RUN_LIVE_INGESTION=1 for live services"
)
def test_live_gemini_neo4j_qdrant_ingestion(tmp_path, record_property):
    """One synthetic chunk, real Gemini extraction/embeddings, Aura and disk Qdrant.

    Uses the models in Saleor JSON, isolated graph IDs and a temporary vector database.
    Provider outages fail this test; they are never converted into skips or mock successes.
    """
    load_dotenv(ROOT / ".env", override=False)
    settings = Settings.from_env().model_copy(
        update={
            "qdrant_url": "",
            "qdrant_path": str(tmp_path / "qdrant"),
            "model_retries": 1,
            "request_timeout": 45,
        }
    )
    settings.require_gemini()
    settings.require_database()
    with create_pipeline(settings) as app:
        folder, corpus = app.collect(setup_project(tmp_path, live=True), tmp_path / "runs")
        assert not corpus.errors and len(corpus.chunks) == 1
        record_property("project_id", corpus.project.project_id)
        record_property("extraction_model", corpus.project.extractor.model)
        result = app.extract(folder)
        assert result.status == "COMPLETE" and result.requirements
        assert any(r.validation == "GROUNDED_CANDIDATE" for r in result.requirements)
        assert all(e.quote in corpus.chunks[0].text for r in result.requirements for e in r.evidence)
        index = app.index(folder, batch_size=1)
        assert index.status == "COMPLETE" and len(index.indexed_chunk_ids) == 1
        counts = app.publish_graph(folder, with_requirements=True)
        assert counts["requirements"] == len(result.requirements) and counts["chunks"] == 1
        assert app.publish_graph(folder, with_requirements=True) == counts
        graph = app.components.graphs.resolve("neo4j")
        rows, _, _ = graph.driver.execute_query(
            "MATCH (:ExtractionRun {id:$eid})-[:PRODUCED]->(r:Requirement)-[e:CITES]->(c:ChunkRef) "
            "RETURN e.quote_verified AS verified, e.semantic_verified AS semantic, c.id AS chunk_id",
            eid=result.id,
            database_=graph.database,
        )
        assert rows and all(r["verified"] and not r["semantic"] for r in rows)
        assert all(r["chunk_id"] == corpus.chunks[0].id for r in rows)
        record_property("requirements", len(result.requirements))
    assert_persisted_vectors(tmp_path / "qdrant", index.profile, corpus)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_INGESTION") != "1", reason="Set RUN_LIVE_INGESTION=1 for live services"
)
def test_live_gemini_embedding_to_persistent_qdrant(tmp_path):
    """Embedding/indexing can be verified independently when extraction is unavailable."""
    load_dotenv(ROOT / ".env", override=False)
    settings = Settings.from_env().model_copy(
        update={
            "qdrant_url": "",
            "qdrant_path": str(tmp_path / "qdrant"),
            "model_retries": 1,
            "request_timeout": 45,
        }
    )
    settings.require_gemini()
    with create_pipeline(settings) as app:
        folder, corpus = app.collect(setup_project(tmp_path, live=True), tmp_path / "runs")
        index = app.index(folder, batch_size=1)
        assert index.status == "COMPLETE"
        assert set(index.indexed_chunk_ids) == {c.id for c in corpus.chunks}
    assert_persisted_vectors(tmp_path / "qdrant", index.profile, corpus)
