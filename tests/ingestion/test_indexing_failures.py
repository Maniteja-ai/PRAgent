"""Embedding/index publication integrity; retrieval is deliberately not exercised."""

import json

import pytest
from qdrant_client import QdrantClient, models

from tests.ingestion.test_library import DeterministicTestEmbeddings, config
from trace_impact import Settings, create_pipeline
from trace_impact.ingestion.storage.qdrant_store import QdrantVectorStore


@pytest.mark.parametrize("invalid", ["wrong_count", "wrong_dimensions", "zero", "infinity"])
def test_invalid_vectors_never_complete_index(tmp_path, invalid):
    class BadEmbeddings(DeterministicTestEmbeddings):
        def embed_documents(self, texts):
            if invalid == "wrong_count":
                return []
            vector = {"wrong_dimensions": [1.0], "zero": [0.0] * 3, "infinity": [float("inf"), 1.0, 0.0]}[
                invalid
            ]
            return [vector for _ in texts]

    path = config(tmp_path, embedding_provider="bad")
    with create_pipeline(Settings(qdrant_path=str(tmp_path / "vectors"))) as app:
        app.components.embeddings.register("bad", BadEmbeddings())
        folder, _ = app.collect(path, tmp_path / "runs")
        with pytest.raises(ValueError):
            app.index(folder)
    assert json.loads((folder / "vector-index.json").read_text())["status"] == "PARTIAL"


@pytest.mark.parametrize("field", ["profile_id", "text_hash"])
def test_mismatched_embedding_cache_is_rejected_without_provider_calls(tmp_path, field):
    path = config(tmp_path, embedding_provider="test")
    embeddings = DeterministicTestEmbeddings()
    with create_pipeline(Settings(qdrant_path=str(tmp_path / "vectors"))) as app:
        app.components.embeddings.register("test", embeddings)
        folder, _ = app.collect(path, tmp_path / "runs")
        app.index(folder)
        calls_before = embeddings.embedded
        cache = next((folder / "embedding-cache").glob("*.json"))
        value = json.loads(cache.read_text())
        value[field] = "wrong-identity"
        cache.write_text(json.dumps(value))
        with pytest.raises(ValueError, match="cache identity"):
            app.index(folder)
        assert embeddings.embedded == calls_before
    assert json.loads((folder / "vector-index.json").read_text())["status"] == "PARTIAL"


@pytest.mark.parametrize("batch_size", [0, 129])
def test_invalid_batch_size_rejected_before_embedding(tmp_path, batch_size):
    path = config(tmp_path, embedding_provider="test")
    embeddings = DeterministicTestEmbeddings()
    with create_pipeline(Settings(qdrant_path=str(tmp_path / "vectors"))) as app:
        app.components.embeddings.register("test", embeddings)
        folder, _ = app.collect(path, tmp_path / "runs")
        with pytest.raises(ValueError, match="batch_size"):
            app.index(folder, batch_size=batch_size)
        assert embeddings.embedded == 0


@pytest.mark.parametrize("mismatch", ["dimensions", "distance"])
def test_incompatible_qdrant_collection_is_rejected(mismatch):
    profile = DeterministicTestEmbeddings.profile
    store = QdrantVectorStore(QdrantClient(":memory:"))
    try:
        store.client.create_collection(
            store.collection(profile),
            vectors_config=models.VectorParams(
                size=4 if mismatch == "dimensions" else 3,
                distance=models.Distance.DOT if mismatch == "distance" else models.Distance.COSINE,
            ),
        )
        with pytest.raises(ValueError):
            store.upsert(profile, [])
    finally:
        store.close()
