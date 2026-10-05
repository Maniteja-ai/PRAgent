import hashlib
import hmac
import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from qdrant_client import QdrantClient, models

from impact_agent.dependencies.agent_bootstrap import AgentBootstrap

CONFIG = Path(__file__).parents[1] / "config" / "default"


def create_test_config(tmp_path: Path) -> Path:
    config_directory = tmp_path / "config"
    shutil.copytree(CONFIG, config_directory)
    knowledge_path = config_directory / "knowledge.json"
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    knowledge["qdrant_url_env"] = None
    knowledge["qdrant_api_key_env"] = None
    knowledge["qdrant_path"] = "vector-store"
    knowledge_path.write_text(json.dumps(knowledge), encoding="utf-8")

    client = QdrantClient(path=str(tmp_path / "vector-store"))
    client.create_collection(
        collection_name="saleor_knowledge",
        vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
    )
    client.close()
    return config_directory


def test_bootstrap_loads_configured_env_file_and_prefers_supplied_environment(
    tmp_path, monkeypatch
):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "NEO4J_URI=neo4j+s://from-file\nNEO4J_DATABASE=file-database\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_DATABASE", raising=False)

    environment = AgentBootstrap._load_environment(
        tmp_path,
        Path(".env"),
        {"NEO4J_DATABASE": "supplied-database"},
    )

    assert environment["NEO4J_URI"] == "neo4j+s://from-file"
    assert environment["NEO4J_DATABASE"] == "supplied-database"


def test_bootstrap_wires_signed_webhook_queue_pipeline_and_worker(tmp_path):
    environment = {
        "GITHUB_WEBHOOK_SECRET": "webhook-secret",
        "GITHUB_TOKEN": "github-token",
        "GEMINI_API_KEY": "gemini-token",
        "QDRANT_URL": "https://qdrant.example.test",
        "QDRANT_API_KEY": "qdrant-token",
        "NEO4J_URI": "neo4j+s://neo4j.example.test",
        "NEO4J_USERNAME": "agent",
        "NEO4J_PASSWORD": "neo4j-token",
        "NEO4J_DATABASE": "saleor",
    }
    application = AgentBootstrap.create(
        create_test_config(tmp_path), tmp_path, environment=environment
    )
    payload = json.dumps(
        {
            "action": "opened",
            "pull_request": {"number": 17},
            "repository": {"full_name": "owner/storefront"},
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(b"webhook-secret", payload, hashlib.sha256).hexdigest()

    try:
        with TestClient(application.webhook_application) as client:
            response = client.post(
                "/webhooks/github",
                content=payload,
                headers={
                    "X-GitHub-Delivery": "delivery-17",
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": f"sha256={signature}",
                },
            )
        assert response.status_code == 202
        assert response.json()["status"] == "QUEUED"
        job = application.beans.webhook_jobs.claim_next(30)
        assert job is not None
        assert job.reference.repository == "owner/storefront"
        assert job.reference.number == 17
        assert application.beans.worker.pipeline is application.beans.pipeline
        assert application.beans.run_store.load(job.run_id) is None
        application.beans.webhook_jobs.complete(job.delivery_id, job.lease_token)
    finally:
        application.close()
