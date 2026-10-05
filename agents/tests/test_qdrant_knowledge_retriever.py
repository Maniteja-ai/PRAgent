import hashlib
import json
from pathlib import Path

import httpx
import pytest
from qdrant_client import QdrantClient, models

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import ChangedFile, PullRequestRef, PullRequestSnapshot
from impact_agent.tools.knowledge.implementations.qdrant_knowledge_retriever import (
    QdrantKnowledgeRetriever,
    QdrantKnowledgeRetrieverFactory,
    QdrantRetrievalError,
)

CONFIG = Path(__file__).parents[1] / "config" / "default"
PULL_REQUEST = PullRequestSnapshot(
    reference=PullRequestRef("owner/storefront", 42),
    title="Change voucher update",
    description="Refresh totals after voucher changes.",
    base_sha="base",
    head_sha="head",
    files=(ChangedFile("src/cart.ts", "modified", 1, 1, "+new"),),
    diff="diff --git a/src/cart.ts b/src/cart.ts",
)


def test_qdrant_retriever_embeds_query_and_returns_scoped_hashed_evidence():
    settings = JsonConfigLoader().load(CONFIG)
    calls: list[httpx.Request] = []
    content = "Checkout voucher contract"

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "qdrant.test" and request.method == "GET":
            return httpx.Response(
                200,
                json={"result": {"config": {"params": {"vectors": {"size": 3}}}}},
                request=request,
            )
        if request.url.host == "generativelanguage.googleapis.com":
            body = json.loads(request.content)
            assert "taskType" not in body
            assert body["outputDimensionality"] == 3
            assert body["content"]["parts"][0]["text"].startswith("task: search result | query: ")
            assert "src/cart.ts" in body["content"]["parts"][0]["text"]
            return httpx.Response(
                200, json={"embedding": {"values": [0.1, 0.2, 0.3]}}, request=request
            )
        body = json.loads(request.content)
        assert request.headers["api-key"] == "qdrant-key"
        assert body["filter"]["must"][0]["match"]["value"] == "saleor-storefront"
        assert len(body["query"]) == 3
        return httpx.Response(
            200,
            json={
                "result": {
                    "points": [
                        {
                            "payload": {
                                "record_id": "chunk-1",
                                "project_id": "saleor-storefront",
                                "content": content,
                                "metadata": {"location": "docs/checkout.md"},
                            }
                        },
                        {
                            "payload": {
                                "record_id": "code-chunk-1",
                                "project_id": "saleor-storefront",
                                "content": "export function applyVoucher() {}",
                                "metadata": {
                                    "kind": "code",
                                    "code_file_id": "file:src/checkout/apply-voucher.ts",
                                    "path": "src/checkout/apply-voucher.ts",
                                    "revision": "abc123",
                                    "symbol_name": "applyVoucher",
                                    "symbol_kind": "Function",
                                    "start_line": 12,
                                    "end_line": 20,
                                    "called_symbols": ["CALLS:refreshCheckout"],
                                    "ui_route_candidates": ["/{channel}/checkout"],
                                    "ui_tags": ["checkout", "apply-voucher"],
                                },
                            }
                        },
                    ]
                }
            },
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    retriever = QdrantKnowledgeRetriever(
        settings.knowledge,
        settings.models,
        settings.runtime.request_timeout_seconds,
        qdrant_url="https://qdrant.test",
        qdrant_api_key="qdrant-key",
        gemini_api_key="gemini-key",
        client=client,
    )

    evidence = retriever.retrieve(PULL_REQUEST)

    assert len(evidence) == 2
    assert evidence[0].evidence_id == "chunk-1"
    assert evidence[0].source == "docs/checkout.md"
    assert evidence[0].content_sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert (
        evidence[1].source
        == "src/checkout/apply-voucher.ts [file:src/checkout/apply-voucher.ts@abc123]"
    )
    assert "Candidate routes: /{channel}/checkout" in evidence[1].content
    assert "Code symbol: Function applyVoucher lines 12-20" in evidence[1].content
    assert "Statically resolved references: CALLS:refreshCheckout" in evidence[1].content
    assert "unconfirmed" in evidence[1].content
    assert evidence[1].content_sha256 == hashlib.sha256(evidence[1].content.encode()).hexdigest()
    assert len(calls) == 3
    client.close()


def test_qdrant_retriever_rejects_evidence_from_another_project():
    settings = JsonConfigLoader().load(CONFIG)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "qdrant.test" and request.method == "GET":
            body = {"result": {"config": {"params": {"vectors": {"size": 3}}}}}
        elif request.url.host == "generativelanguage.googleapis.com":
            body = {"embedding": {"values": [0.1, 0.2, 0.3]}}
        else:
            body = {
                "result": {
                    "points": [
                        {
                            "payload": {
                                "record_id": "chunk-foreign",
                                "project_id": "another-project",
                                "content": "Foreign data",
                            }
                        }
                    ]
                }
            }
        return httpx.Response(200, json=body, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    retriever = QdrantKnowledgeRetriever(
        settings.knowledge,
        settings.models,
        settings.runtime.request_timeout_seconds,
        qdrant_url="https://qdrant.test",
        qdrant_api_key=None,
        gemini_api_key="gemini-key",
        client=client,
    )

    with pytest.raises(QdrantRetrievalError, match="unscoped evidence"):
        retriever.retrieve(PULL_REQUEST)
    client.close()


def test_qdrant_factory_uses_configured_environment_variable_names():
    settings = JsonConfigLoader().load(CONFIG)
    remote_knowledge = settings.knowledge.model_copy(
        update={
            "qdrant_url_env": "QDRANT_URL",
            "qdrant_api_key_env": "QDRANT_API_KEY",
            "qdrant_path": None,
        }
    )
    environment = {
        "QDRANT_URL": "https://qdrant.test",
        "QDRANT_API_KEY": "qdrant-key",
        settings.models.api_key_env: "gemini-key",
    }

    retriever = QdrantKnowledgeRetrieverFactory.create(
        remote_knowledge,
        settings.models,
        settings.runtime.request_timeout_seconds,
        environment,
    )

    assert isinstance(retriever, QdrantKnowledgeRetriever)
    retriever.close()


def test_qdrant_retriever_reads_from_local_persistent_collection(tmp_path):
    settings = JsonConfigLoader().load(CONFIG)
    path = tmp_path / "vector-store"
    writer = QdrantClient(path=str(path))
    writer.create_collection(
        collection_name=settings.knowledge.collection,
        vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
    )
    writer.upsert(
        collection_name=settings.knowledge.collection,
        points=[
            models.PointStruct(
                id=1,
                vector=[0.1, 0.2, 0.3],
                payload={
                    "record_id": "chunk-local-1",
                    "project_id": settings.knowledge.project,
                    "content": "Local checkout knowledge",
                    "metadata": {"location": "docs/checkout.md"},
                },
            )
        ],
    )
    writer.close()
    local_client = QdrantClient(path=str(path))
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"embedding": {"values": [0.1, 0.2, 0.3]}},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    local_knowledge = settings.knowledge.model_copy(
        update={
            "qdrant_url_env": None,
            "qdrant_api_key_env": None,
            "qdrant_path": path,
        }
    )
    retriever = QdrantKnowledgeRetriever(
        local_knowledge,
        settings.models,
        settings.runtime.request_timeout_seconds,
        qdrant_url=None,
        qdrant_api_key=None,
        gemini_api_key="gemini-key",
        client=client,
        local_qdrant_client=local_client,
    )

    evidence = retriever.retrieve(PULL_REQUEST)

    assert len(evidence) == 1
    assert evidence[0].evidence_id == "chunk-local-1"
    assert evidence[0].source == "docs/checkout.md"
    assert requests[0].url.host == "generativelanguage.googleapis.com"
    retriever.close()


def test_qdrant_factory_rejects_empty_local_store(tmp_path):
    settings = JsonConfigLoader().load(CONFIG)
    path = tmp_path / "empty-vector-store"
    path.mkdir()
    local_knowledge = settings.knowledge.model_copy(
        update={
            "qdrant_url_env": None,
            "qdrant_api_key_env": None,
            "qdrant_path": path,
        }
    )

    with pytest.raises(ValueError, match="Run ingestion to publish the vector collection"):
        QdrantKnowledgeRetrieverFactory.create(
            local_knowledge,
            settings.models,
            settings.runtime.request_timeout_seconds,
            {settings.models.api_key_env: "gemini-key"},
        )
