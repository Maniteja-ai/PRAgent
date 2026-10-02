"""Extensions are selected through user configuration, without changing the pipeline."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from qdrant_client import QdrantClient

from trace_impact import create_pipeline
from trace_impact.errors import ConfigurationError
from trace_impact.implementations.loaders import GitHubFileLoader, WebLoader
from trace_impact.implementations.storage.artifacts import FileArtifactRepository
from trace_impact.implementations.storage.qdrant import QdrantVectorStore
from trace_impact.models import (
    EmbeddingProfile,
    Extraction,
    RawDocument,
    VectorIndexRun,
    load_project,
)
from trace_impact.registry import Registry

ROOT = Path(__file__).resolve().parents[1]


def config(tmp_path, **changes):
    project = load_project(ROOT / "projects/example/project.json")
    project = project.model_copy(update=changes)
    (tmp_path / "spec.md").write_text(
        (ROOT / "projects/example/spec.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    path = tmp_path / "project.json"
    path.write_text(project.model_dump_json(), encoding="utf-8")
    return path


def test_custom_parser_is_a_real_external_extension(tmp_path):
    spec = importlib.util.spec_from_file_location("custom_parser_example", ROOT / "examples/custom_parser.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with create_pipeline() as pipeline:
        pipeline.components.parsers.register("json_features", module.JsonFeatureParser())
        _, corpus = pipeline.collect(ROOT / "projects/plugin-example/project.yaml", tmp_path)
    assert not corpus.errors
    assert "renew a loan" in "\n".join(c.text for c in corpus.chunks)


class InlineLoader:
    version = "unit-test-inline-v1"

    def load(self, source, project, config_dir):
        for name in ["first", "second"]:
            yield RawDocument(
                key=name,
                location=f"inline:{name}",
                content=f"# {name}\n\nA visitor can search the catalog by book title.".encode(),
            )


class LabeledTestExtractor:
    provider, model, fingerprint, prompt_version = "test-only", "none", "empty-v1", "test"

    def extract(self, chunk, snapshot, scope):
        return Extraction(requirements=[], no_requirement_reason="Test double only")


def test_custom_loader_can_return_multiple_documents_and_custom_extractor_is_selected(tmp_path):
    path = config(tmp_path, extractor="test_extractor")
    data = json.loads(path.read_text())
    data["sources"][0]["loader"] = "inline"
    path.write_text(json.dumps(data))
    with create_pipeline() as pipeline:
        pipeline.components.loaders.register("inline", InlineLoader())
        pipeline.components.extractors.register("test_extractor", LabeledTestExtractor())
        folder, corpus = pipeline.collect(path, tmp_path / "runs")
        result = pipeline.extract(folder)
    assert len(corpus.snapshots) == 2
    assert len({s.id for s in corpus.snapshots}) == 2
    assert result.status == "COMPLETE" and result.provider == "test-only"


def test_invalid_names_fail_before_creating_run_or_loading_source(tmp_path):
    path = config(tmp_path, chunker="does_not_exist")
    with create_pipeline() as pipeline:
        with pytest.raises(ConfigurationError, match="Unknown chunker.*section"):
            pipeline.collect(path, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_github_loader_pins_revision_and_reuses_http_host_policy(tmp_path):
    project = load_project(ROOT / "projects/saleor/project.json")
    source = project.sources[0]
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, text="# Test README\n\nA visitor can search a product catalog.")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        loader = GitHubFileLoader(WebLoader(client))
        result = list(loader.load(source, project, tmp_path))
        source.options["revision"] = "main"
        with pytest.raises(ValueError, match="pinned"):
            list(loader.load(source, project, tmp_path))
    assert len(urls) == 1 and source.version in urls[0]
    assert result[0].key == "README.md"


def test_registry_is_lazy_and_closes_created_resources():
    events = []

    class Resource:
        def __init__(self):
            events.append("created")

        def close(self):
            events.append("closed")

    registry = Registry("test")
    registry.register_factory("resource", Resource)
    registry.require("resource")
    assert events == []
    assert registry.resolve("resource") is registry.resolve("resource")
    with pytest.raises(ConfigurationError, match="already"):
        registry.register("resource", object())
    registry.register("alias", registry.resolve("resource"))
    registry.resolve("alias")
    registry.close()
    assert events == ["created", "closed"]


class DeterministicTestEmbeddings:
    """Test vectors only; not a production semantic model or a registered built-in."""

    profile = EmbeddingProfile(provider="unit-test", model="keyword-fixture", dimensions=3)

    def __init__(self):
        self.embedded = 0

    def embed_documents(self, texts):
        self.embedded += len(texts)
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [1.0, float("search" in text.lower()), float("loan" in text.lower())]


def test_real_local_qdrant_roundtrip_is_scoped_and_resume_reuses_embeddings(tmp_path):
    path = config(tmp_path, embedding_provider="test_embeddings")
    embeddings = DeterministicTestEmbeddings()
    qdrant = QdrantVectorStore(QdrantClient(path=str(tmp_path / "qdrant")))
    with create_pipeline() as pipeline:
        pipeline.components.embeddings.register("test_embeddings", embeddings)
        pipeline.components.vectors.register("test_qdrant", qdrant)
        data = json.loads(path.read_text())
        data["storage"]["vector"] = "test_qdrant"
        path.write_text(json.dumps(data))
        folder, corpus = pipeline.collect(path, tmp_path / "runs")
        first = pipeline.index(folder, batch_size=2)
        assert first.status == "COMPLETE"
        pipeline.index(folder, batch_size=2)
        assert embeddings.embedded == len(corpus.chunks)
        hits = pipeline.search(folder, "search", limit=2)
        assert hits and all(h.chunk_id in {c.id for c in corpus.chunks} for h in hits)
        assert not qdrant.search(embeddings.profile, "other-project", corpus.run_id, [1, 1, 0], 5)
        assert not qdrant.search(embeddings.profile, corpus.project.project_id, "other-run", [1, 1, 0], 5)
        first.status = "PARTIAL"
        FileArtifactRepository().write(folder / "vector-index.json", first)
        with pytest.raises(ValueError, match="complete index"):
            pipeline.search(folder, "search")


def test_invalid_embedding_batch_cannot_publish_complete_index(tmp_path):
    class BadEmbedding(DeterministicTestEmbeddings):
        def embed_documents(self, texts):
            return [[float("nan"), 0, 1] for _ in texts]

    path = config(tmp_path, embedding_provider="bad")
    with create_pipeline() as pipeline:
        pipeline.components.embeddings.register("bad", BadEmbedding())
        pipeline.components.vectors.register("memory_test", QdrantVectorStore(QdrantClient(":memory:")))
        data = json.loads(path.read_text())
        data["storage"]["vector"] = "memory_test"
        path.write_text(json.dumps(data))
        folder, _ = pipeline.collect(path, tmp_path / "runs")
        with pytest.raises(ValueError, match="finite"):
            pipeline.index(folder)
        assert FileArtifactRepository().read(folder / "vector-index.json", VectorIndexRun).status == "PARTIAL"


def test_vector_publication_failure_can_resume_without_reembedding(tmp_path):
    class FailVerificationOnce(QdrantVectorStore):
        fail = True

        def verify(self, profile, records):
            if self.fail:
                self.fail = False
                return False
            return super().verify(profile, records)

    path = config(tmp_path, embedding_provider="test_embeddings")
    embeddings = DeterministicTestEmbeddings()
    store = FailVerificationOnce(QdrantClient(":memory:"))
    data = json.loads(path.read_text())
    data["storage"]["vector"] = "test_store"
    path.write_text(json.dumps(data))
    with create_pipeline() as pipeline:
        pipeline.components.embeddings.register("test_embeddings", embeddings)
        pipeline.components.vectors.register("test_store", store)
        folder, corpus = pipeline.collect(path, tmp_path / "runs")
        with pytest.raises(ValueError, match="read-back"):
            pipeline.index(folder, batch_size=1)
        with pytest.raises(ValueError, match="complete index"):
            pipeline.search(folder, "search")
        assert pipeline.index(folder, batch_size=1).status == "COMPLETE"
        assert embeddings.embedded == len(corpus.chunks)
        assert store.client.count(store.collection(embeddings.profile)).count == len(corpus.chunks)


def test_langchain_embeddings_wire_contract_and_client_cleanup(monkeypatch):
    from trace_impact.config import Settings

    captured = []
    clients = []
    real_client = httpx.Client

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    class TrackedClient(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(self)

    monkeypatch.setattr("trace_impact.bootstrap.httpx.Client", TrackedClient)
    settings = Settings(
        openai_api_key="unit-test-only",
        embedding_model="text-embedding-3-small",
        embedding_dimensions=3,
        model_retries=0,
    )
    with create_pipeline(settings) as pipeline:
        provider = pipeline.components.embeddings.resolve("openai")
        assert provider.embed_documents(["search catalog"]) == [[1.0, 0.0, 0.0]]
        assert provider.embed_query("search") == [1.0, 0.0, 0.0]
    assert len(captured) == 2 and all(r["dimensions"] == 3 for r in captured)
    assert clients and all(client.is_closed for client in clients)
