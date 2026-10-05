"""Offline end-to-end check from vector/graph retrieval through a saved PR report."""

import hashlib
import hmac
import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import (
    BehaviorResult,
    BehaviorVerification,
    ChangedFile,
    Decision,
    Evidence,
    Finding,
    PullRequestRef,
    PullRequestSnapshot,
)
from impact_agent.guardrails.implementations.basic_guardrail import BasicGuardrail
from impact_agent.pipeline.implementations.langgraph_agent_pipeline import LangGraphAgentPipeline
from impact_agent.report.implementations.markdown_report_formatter import MarkdownReportFormatter
from impact_agent.run_history.implementations.sqlite_run_history_store import SQLiteRunHistoryStore
from impact_agent.tools.knowledge.implementations.composite_knowledge_retriever import (
    CompositeKnowledgeRetriever,
)
from impact_agent.tools.knowledge.implementations.neo4j_code_graph_retriever import (
    Neo4jCodeGraphRetriever,
)
from impact_agent.tools.knowledge.implementations.qdrant_knowledge_retriever import (
    QdrantKnowledgeRetriever,
)
from impact_agent.webhook.implementations.fastapi_app import create_webhook_app
from impact_agent.webhook.implementations.job_worker import WebhookJobWorker
from impact_agent.webhook.implementations.receiver_factory import GitHubWebhookReceiverFactory
from impact_agent.webhook.implementations.sqlite_job_queue import SQLiteWebhookJobQueue

CONFIG = Path(__file__).parents[1] / "config" / "default"
PULL_REQUEST = PullRequestSnapshot(
    PullRequestRef("owner/storefront", 42),
    "Refresh voucher totals",
    "Refresh checkout after applying a voucher.",
    "base-sha",
    "head-sha",
    (
        ChangedFile(
            "src/cart.ts",
            "modified",
            4,
            1,
            "@@ -10,1 +10,4 @@\n+export function applyVoucher() {\n+  refreshCheckout();\n+}",
        ),
    ),
    "diff --git a/src/cart.ts b/src/cart.ts",
)


class _Record(dict[str, Any]):
    def data(self) -> dict[str, Any]:
        return self


class _Transaction:
    def __init__(self, driver: "_Driver") -> None:
        self.driver = driver

    def run(self, query: str, _parameters: dict[str, Any]) -> list[_Record]:
        if "nodeTypeProperties" in query:
            return [_Record(nodeLabels=["IngestedEntity"], propertyName="kind")]
        if "relTypeProperties" in query:
            return [_Record(relType="CODE_RELATIONSHIP", propertyName="kind")]
        return self.driver.records

    def commit(self) -> None:
        self.driver.commits += 1

    def rollback(self) -> None:
        self.driver.rollbacks += 1


class _Session:
    def __init__(self, driver: "_Driver") -> None:
        self.driver = driver

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def begin_transaction(self, **_kwargs: object) -> _Transaction:
        return _Transaction(self.driver)


class _Driver:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.records = [
            _Record(
                changed_path="src/cart.ts",
                related_files=[{"path": "src/checkout.ts", "relationship_kinds": ["CALLS"]}],
                related_symbols=[
                    {
                        "name": "refreshCheckout",
                        "symbol_kind": "Function",
                        "path": "src/checkout.ts",
                        "start_line": 9,
                        "end_line": 16,
                        "relationship_kinds": ["CALLS"],
                    }
                ],
                confirmed_ui_mappings=[
                    {
                        "code_path": "src/cart.ts",
                        "url": "https://store.example/cart",
                        "title": "Cart",
                        "basis": "framework_route",
                        "confidence": 1.0,
                        "evidence_ids": ["route-evidence"],
                    }
                ],
            )
        ]

    def session(self, **_kwargs: object) -> _Session:
        return _Session(self)

    def close(self) -> None:
        return None


class _Planner:
    def create_query(
        self,
        _pull_request: PullRequestSnapshot,
        _schema: str,
        _indexed_revision: str,
        maximum_rows: int,
        maximum_call_depth: int,
    ) -> str:
        assert maximum_call_depth == 3
        return (
            "MATCH (changed:CodeFile) "
            "WHERE changed.path IN $changed_paths AND changed.revision = $revision "
            "RETURN changed.path AS changed_path, [] AS related_files, "
            "[] AS related_symbols, [] AS confirmed_ui_mappings "
            f"LIMIT {maximum_rows}"
        )

    def close(self) -> None:
        return None


class _PullRequestProvider:
    def fetch(self, reference: PullRequestRef) -> PullRequestSnapshot:
        assert reference == PULL_REQUEST.reference
        return PULL_REQUEST


class _DecisionModel:
    def decide(self, pull_request: PullRequestSnapshot, evidence: tuple[Evidence, ...]) -> Decision:
        assert pull_request == PULL_REQUEST
        vector_evidence = next(item for item in evidence if item.source.startswith("src/cart.ts ["))
        graph_evidence = next(item for item in evidence if item.source.startswith("neo4j://"))
        diff_evidence = next(item for item in evidence if item.source.startswith("pr-diff://"))
        assert "applyVoucher" in vector_evidence.content
        assert "refreshCheckout" in graph_evidence.content
        assert graph_evidence.confirmed_code_ui_mappings
        return Decision(
            "The voucher change reaches checkout refresh and the cart route.",
            (
                Finding(
                    "Cart totals may be affected",
                    "Applying a voucher calls the checkout refresh function.",
                    (
                        vector_evidence.evidence_id,
                        graph_evidence.evidence_id,
                        diff_evidence.evidence_id,
                    ),
                ),
            ),
        )


class _Browser:
    def explore(self, _pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        text = "Cart page route was reachable in the browser."
        return (
            Evidence(
                "browser:cart",
                "https://store.example/cart",
                text,
                hashlib.sha256(text.encode()).hexdigest(),
            ),
        )


class _BehaviorVerifier:
    def verify(self, pull_request, findings, evidence) -> BehaviorVerification:
        assert pull_request == PULL_REQUEST
        assert findings
        assert any(item.confirmed_code_ui_mappings for item in evidence)
        return BehaviorVerification(
            results=(
                BehaviorResult(
                    "cart-voucher-refresh",
                    "PASS",
                    "The configured cart voucher refresh check passed.",
                    verified_checks=(".discount-label is visible", "URL contains '/cart'"),
                ),
            )
        )


def test_retrieval_adapters_flow_through_langgraph_into_persisted_report(tmp_path: Path) -> None:
    settings = JsonConfigLoader().load(CONFIG)
    settings = settings.model_copy(
        update={
            "agent": settings.agent.model_copy(
                update={"browser_enabled": True, "behavior_checks_enabled": True}
            ),
            "knowledge": settings.knowledge.model_copy(
                update={"qdrant_path": tmp_path / "vectors", "max_evidence": 12}
            ),
        }
    )

    writer = QdrantClient(path=str(tmp_path / "vectors"))
    writer.create_collection(
        collection_name=settings.knowledge.collection,
        vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
    )
    writer.upsert(
        collection_name=settings.knowledge.collection,
        points=[
            models.PointStruct(
                id=1,
                vector=[0.3, 0.4, 0.5],
                payload={
                    "record_id": "qdrant:apply-voucher",
                    "project_id": settings.knowledge.project,
                    "content": "export function applyVoucher() { refreshCheckout(); }",
                    "metadata": {
                        "kind": "code",
                        "code_file_id": "file:src/cart.ts",
                        "path": "src/cart.ts",
                        "revision": "indexed-revision",
                        "symbol_name": "applyVoucher",
                        "symbol_kind": "Function",
                        "start_line": 2,
                        "end_line": 7,
                        "called_symbols": ["CALLS:refreshCheckout"],
                    },
                },
            )
        ],
    )
    writer.close()

    def fake_embedding(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "generativelanguage.googleapis.com"
        return httpx.Response(200, json={"embedding": {"values": [0.3, 0.4, 0.5]}}, request=request)

    vector_reader = QdrantClient(path=str(tmp_path / "vectors"))
    vector_retriever = QdrantKnowledgeRetriever(
        settings.knowledge,
        settings.models,
        settings.runtime.request_timeout_seconds,
        qdrant_url=None,
        qdrant_api_key=None,
        gemini_api_key="offline-test-key",
        client=httpx.Client(transport=httpx.MockTransport(fake_embedding)),
        local_qdrant_client=vector_reader,
    )
    graph_driver = _Driver()
    graph_retriever = Neo4jCodeGraphRetriever(
        graph_driver,
        "saleor-test",
        "indexed-revision",
        settings.knowledge.max_evidence,
        _Planner(),
        max_call_depth=settings.graph_database.max_call_depth,
    )
    history = SQLiteRunHistoryStore(tmp_path / "runs.sqlite")
    queue = SQLiteWebhookJobQueue(tmp_path / "webhook-jobs.sqlite")
    pipeline = LangGraphAgentPipeline(
        settings=settings,
        pull_requests=_PullRequestProvider(),
        knowledge=CompositeKnowledgeRetriever(
            (vector_retriever, graph_retriever), settings.knowledge.max_evidence
        ),
        decision_model=_DecisionModel(),
        guardrails=BasicGuardrail(settings.guardrails),
        report_formatter=MarkdownReportFormatter(),
        run_store=history,
        stage_recorder=history,
        browser=_Browser(),
        behavior_verifier=_BehaviorVerifier(),
    )

    secret = "synthetic-webhook-secret"
    webhook = create_webhook_app(
        settings.webhook,
        GitHubWebhookReceiverFactory.create(
            settings.webhook, {settings.webhook.secret_env: secret}
        ),
        queue,
    )
    payload = json.dumps(
        {
            "action": "opened",
            "pull_request": {"number": PULL_REQUEST.reference.number},
            "repository": {"full_name": PULL_REQUEST.reference.repository},
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    with TestClient(webhook) as client:
        accepted = client.post(
            "/webhooks/github",
            content=payload,
            headers={
                "X-GitHub-Delivery": "offline-e2e-delivery-42",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": f"sha256={signature}",
            },
        )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "QUEUED"
    execution = WebhookJobWorker(queue, pipeline, settings.runtime).run_once()
    assert execution.status == "COMPLETED"
    assert execution.run_id is not None
    report = history.load(execution.run_id)
    assert report is not None

    assert report.status.value == "COMPLETED"
    assert report.gaps == ()
    assert report.behavior_results[0].status == "PASS"
    assert len(report.evidence) == 4
    assert "Cart totals may be affected" in report.rendered_report
    assert "cart-voucher-refresh" in report.rendered_report
    assert ".discount-label is visible" in report.rendered_report
    assert "only the listed assertions" in report.rendered_report
    assert history.load(report.run_id) == report
    with sqlite3.connect(tmp_path / "runs.sqlite") as connection:
        stages = connection.execute(
            "SELECT stage FROM stage_evaluations WHERE run_id = ? ORDER BY id",
            (report.run_id,),
        ).fetchall()
    assert len(stages) == 7
    assert graph_driver.rollbacks == 0
    vector_retriever.close()
    graph_retriever.close()
