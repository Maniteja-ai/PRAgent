import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import PullRequestRef, WebhookJobResult
from impact_agent.webhook.implementations.fastapi_app import create_webhook_app
from impact_agent.webhook.implementations.github_receiver import GitHubWebhookReceiver
from impact_agent.webhook.implementations.receiver_factory import GitHubWebhookReceiverFactory
from impact_agent.webhook.implementations.sqlite_job_queue import SQLiteWebhookJobQueue
from impact_agent.webhook.interface.webhook_job_handler import WebhookDeliveryConflict

CONFIG = Path(__file__).parents[1] / "config" / "default"
SECRET = "local-test-webhook-secret"


def payload_bytes(action: str = "opened") -> bytes:
    return json.dumps(
        {
            "action": action,
            "pull_request": {"number": 42},
            "repository": {"full_name": "owner/storefront"},
        },
        separators=(",", ":"),
    ).encode()


def signed_headers(body: bytes, *, event: str = "pull_request") -> dict[str, str]:
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-GitHub-Delivery": "delivery-42",
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": f"sha256={signature}",
    }


def test_receiver_accepts_a_valid_configured_pull_request_event():
    config = JsonConfigLoader().load(CONFIG).webhook
    body = payload_bytes()

    result = GitHubWebhookReceiver(config, SECRET).receive(signed_headers(body), body)

    assert result.accepted
    assert result.delivery_id == "delivery-42"
    assert result.reference == PullRequestRef("owner/storefront", 42)


def test_receiver_rejects_tampering_and_missing_secret():
    config = JsonConfigLoader().load(CONFIG).webhook
    body = payload_bytes()
    headers = signed_headers(body)
    tampered_body = body.replace(b"opened", b"reopened")

    result = GitHubWebhookReceiver(config, SECRET).receive(headers, tampered_body)

    assert result.accepted is False
    assert result.reason == "INVALID_SIGNATURE"


def test_receiver_rejects_non_ascii_signature_without_raising():
    config = JsonConfigLoader().load(CONFIG).webhook
    body = payload_bytes()
    headers = {**signed_headers(body), "X-Hub-Signature-256": "sha256=é"}

    result = GitHubWebhookReceiver(config, SECRET).receive(headers, body)

    assert result.reason == "INVALID_SIGNATURE"


def test_receiver_ignores_unsupported_action_and_event():
    config = JsonConfigLoader().load(CONFIG).webhook
    receiver = GitHubWebhookReceiver(config, SECRET)
    body = payload_bytes(action="closed")

    action_result = receiver.receive(signed_headers(body), body)
    event_result = receiver.receive(signed_headers(body, event="issues"), body)

    assert action_result.reason == "UNSUPPORTED_ACTION"
    assert event_result.reason == "UNSUPPORTED_EVENT"


def test_receiver_rejects_oversized_or_invalid_payload():
    config = JsonConfigLoader().load(CONFIG).webhook
    bounded_config = config.model_copy(update={"max_body_bytes": 1_024})
    receiver = GitHubWebhookReceiver(bounded_config, SECRET)

    large_result = receiver.receive(signed_headers(b""), b"x" * 1_025)
    bad_json = b"{"
    invalid_result = receiver.receive(signed_headers(bad_json), bad_json)

    assert large_result.reason == "BODY_TOO_LARGE"
    assert invalid_result.reason == "INVALID_JSON"


class FakeJobHandler:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, PullRequestRef]] = []

    def submit(self, delivery_id: str, reference: PullRequestRef) -> WebhookJobResult:
        self.submissions.append((delivery_id, reference))
        return WebhookJobResult("QUEUED")


def test_http_route_queues_valid_events_and_rejects_bad_signatures():
    config = JsonConfigLoader().load(CONFIG)
    job_handler = FakeJobHandler()
    receiver = GitHubWebhookReceiver(config.webhook, SECRET)
    client = TestClient(create_webhook_app(config.webhook, receiver, job_handler))
    body = payload_bytes()

    accepted = client.post("/webhooks/github", content=body, headers=signed_headers(body))
    invalid = client.post(
        "/webhooks/github",
        content=body,
        headers={**signed_headers(body), "X-Hub-Signature-256": "sha256=wrong"},
    )

    assert accepted.status_code == 202
    assert accepted.json() == {"status": "QUEUED", "delivery_id": "delivery-42"}
    assert invalid.status_code == 401
    assert job_handler.submissions == [("delivery-42", PullRequestRef("owner/storefront", 42))]


def test_http_route_limits_declared_request_size():
    config = JsonConfigLoader().load(CONFIG)
    jobs = FakeJobHandler()
    client = TestClient(
        create_webhook_app(config.webhook, GitHubWebhookReceiver(config.webhook, SECRET), jobs)
    )

    response = client.post(
        "/webhooks/github",
        content=b"{}",
        headers={"Content-Length": str(config.webhook.max_body_bytes + 1)},
    )

    assert response.status_code == 413
    assert jobs.submissions == []


def test_durable_job_queue_is_idempotent_and_checks_delivery_id_reuse(tmp_path):
    queue = SQLiteWebhookJobQueue(tmp_path / "jobs.sqlite")
    reference = PullRequestRef("owner/storefront", 42)

    assert queue.submit("delivery-42", reference).status == "QUEUED"
    assert queue.submit("delivery-42", reference).status == "DUPLICATE"
    try:
        queue.submit("delivery-42", PullRequestRef("owner/storefront", 43))
    except WebhookDeliveryConflict:
        pass
    else:
        raise AssertionError("Reusing a delivery ID for another PR must be rejected")


def test_disabled_receiver_does_not_require_a_secret():
    config = JsonConfigLoader().load(CONFIG).webhook.model_copy(update={"enabled": False})
    receiver = GitHubWebhookReceiver(config, "")

    result = receiver.receive({}, b"")

    assert result.reason == "WEBHOOK_DISABLED"


def test_receiver_factory_reads_secret_by_configured_environment_name():
    config = JsonConfigLoader().load(CONFIG).webhook
    receiver = GitHubWebhookReceiverFactory.create(
        config,
        {config.secret_env: SECRET},
    )
    body = payload_bytes()

    assert receiver.receive(signed_headers(body), body).accepted


def test_receiver_factory_fails_closed_when_secret_is_missing():
    config = JsonConfigLoader().load(CONFIG).webhook

    try:
        GitHubWebhookReceiverFactory.create(config, {})
    except ValueError as error:
        assert config.secret_env in str(error)
    else:
        raise AssertionError("Enabled webhooks must not start without a secret")


def test_http_route_uses_durable_queue_to_deduplicate_deliveries(tmp_path):
    config = JsonConfigLoader().load(CONFIG)
    queue = SQLiteWebhookJobQueue(tmp_path / "jobs.sqlite")
    receiver = GitHubWebhookReceiver(config.webhook, SECRET)
    client = TestClient(create_webhook_app(config.webhook, receiver, queue))
    body = payload_bytes()
    headers = signed_headers(body)

    first = client.post("/webhooks/github", content=body, headers=headers)
    repeated = client.post("/webhooks/github", content=body, headers=headers)

    assert first.json()["status"] == "QUEUED"
    assert repeated.json()["status"] == "DUPLICATE"


def test_http_route_rejects_delivery_id_reused_for_another_pr(tmp_path):
    config = JsonConfigLoader().load(CONFIG)
    queue = SQLiteWebhookJobQueue(tmp_path / "jobs.sqlite")
    queue.submit("delivery-42", PullRequestRef("owner/storefront", 43))
    receiver = GitHubWebhookReceiver(config.webhook, SECRET)
    client = TestClient(create_webhook_app(config.webhook, receiver, queue))
    body = payload_bytes()

    response = client.post("/webhooks/github", content=body, headers=signed_headers(body))

    assert response.status_code == 409
