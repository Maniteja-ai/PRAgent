import hashlib
import hmac
import json
import sqlite3
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from trace_coordinator.github_webhook import (
    GitHubAppTokenProvider,
    GitHubCommentPublisher,
    WebhookConfig,
    WebhookRejected,
    WebhookService,
    run_next_job,
)


def config(tmp_path):
    return WebhookConfig(
        allowed_repositories=("saleor/storefront",),
        database_file=str(tmp_path / "jobs.sqlite"),
        coordinator_config_file=str(tmp_path / "coordinator.json"),
        output_directory=str(tmp_path / "output"),
        project_id="saleor-storefront",
    )


def delivery(secret, *, delivery_id="12345678-abcd", action="opened", head="b" * 40):
    payload = json.dumps(
        {
            "action": action,
            "number": 1199,
            "repository": {"full_name": "saleor/storefront"},
            "pull_request": {"base": {"sha": "a" * 40}, "head": {"sha": head}},
            "installation": {"id": 42},
        },
        separators=(",", ":"),
    ).encode()
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "pull_request",
    }
    return headers, payload


def test_webhook_verifies_signature_deduplicates_and_stores_no_pr_text(tmp_path):
    service = WebhookService(config(tmp_path), secret="secret")
    headers, payload = delivery("secret")
    first = service.accept(headers, payload)
    second = service.accept(headers, payload)
    assert first["status"] == "QUEUED"
    assert second["status"] == "DUPLICATE"
    db = sqlite3.connect(tmp_path / "jobs.sqlite")
    try:
        row = db.execute("SELECT repository,pull_request,head_sha,status FROM jobs").fetchone()
        columns = [item[1] for item in db.execute("PRAGMA table_info(jobs)")]
    finally:
        db.close()
    assert row == ("saleor/storefront", 1199, "b" * 40, "QUEUED")
    assert "body" not in columns and "title" not in columns


def test_webhook_rejects_bad_signature_repo_and_reused_delivery(tmp_path):
    service = WebhookService(config(tmp_path), secret="secret")
    headers, payload = delivery("wrong")
    with pytest.raises(WebhookRejected, match="signature"):
        service.accept(headers, payload)

    headers, payload = delivery("secret")
    changed = json.loads(payload)
    changed["repository"]["full_name"] = "evil/repository"
    changed = json.dumps(changed).encode()
    headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"secret", changed, hashlib.sha256).hexdigest()
    with pytest.raises(WebhookRejected, match="not allowed"):
        service.accept(headers, changed)

    headers, payload = delivery("secret")
    service.accept(headers, payload)
    headers2, payload2 = delivery("secret", delivery_id=headers["X-GitHub-Delivery"], head="c" * 40)
    with pytest.raises(WebhookRejected, match="reused"):
        service.accept(headers2, payload2)


def test_new_head_supersedes_queued_job_and_claim_is_atomic(tmp_path):
    service = WebhookService(config(tmp_path), secret="secret")
    headers, payload = delivery("secret", delivery_id="delivery-0001", head="b" * 40)
    service.accept(headers, payload)
    headers, payload = delivery("secret", delivery_id="delivery-0002", action="synchronize", head="c" * 40)
    service.accept(headers, payload)
    db = sqlite3.connect(tmp_path / "jobs.sqlite")
    try:
        statuses = db.execute("SELECT head_sha,status FROM jobs ORDER BY updated").fetchall()
    finally:
        db.close()
    assert statuses == [("b" * 40, "SUPERSEDED"), ("c" * 40, "QUEUED")]
    claimed = service.store.claim()
    assert claimed["head_sha"] == "c" * 40
    assert service.store.claim() is None


def test_comment_publisher_updates_one_marker_comment():
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": 9, "body": "<!-- trace-impact-report -->\nold"}],
            )
        return httpx.Response(200, json={"id": 9})

    class Auth:
        def token(self, installation_id):
            assert installation_id == 42
            return "installation-token"

    publisher = GitHubCommentPublisher(Auth(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = publisher.publish(
        {"repository": "saleor/storefront", "pull_request": 1199, "installation_id": 42},
        "report",
    )
    assert result == {"status": "UPDATED", "comment_id": 9}
    assert [request.method for request in calls] == ["GET", "PATCH"]
    assert calls[1].headers["authorization"] == "Bearer installation-token"


def test_worker_supersedes_job_when_pr_revision_changed(tmp_path):
    selected = config(tmp_path)
    config_file = tmp_path / "webhook.json"
    config_file.write_text(selected.model_dump_json(), encoding="utf-8")
    service = WebhookService(selected, secret="secret")
    headers, payload = delivery("secret")
    service.accept(headers, payload)

    class Auth:
        def token(self, installation_id):
            assert installation_id == 42
            return "installation-token"

    def handler(request):
        assert request.headers["authorization"] == "Bearer installation-token"
        return httpx.Response(
            200,
            json={"number": 1199, "base": {"sha": "a" * 40}, "head": {"sha": "c" * 40}},
        )

    result = run_next_job(
        config_file,
        auth=Auth(),
        github_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result["status"] == "SUPERSEDED"
    database = sqlite3.connect(tmp_path / "jobs.sqlite")
    try:
        status, error = database.execute("SELECT status,error_code FROM jobs").fetchone()
    finally:
        database.close()
    assert (status, error) == ("SUPERSEDED", "PR_REVISION_CHANGED")


def test_webhook_ignores_other_events_and_actions_and_rejects_bad_payload(tmp_path):
    selected = config(tmp_path)
    service = WebhookService(selected, secret="secret")
    headers, payload = delivery("secret", action="closed")
    assert service.accept(headers, payload)["status"] == "IGNORED"
    headers["X-GitHub-Event"] = "push"
    assert service.accept(headers, payload)["status"] == "IGNORED"

    malformed = b"{}"
    signature = "sha256=" + hmac.new(b"secret", malformed, hashlib.sha256).hexdigest()
    with pytest.raises(WebhookRejected, match="Malformed"):
        service.accept(
            {
                "X-Hub-Signature-256": signature,
                "X-GitHub-Delivery": "malformed-0001",
                "X-GitHub-Event": "pull_request",
            },
            malformed,
        )
    service.config = selected.model_copy(update={"max_payload_bytes": 1000})
    with pytest.raises(WebhookRejected, match="exceeds"):
        service.accept({}, b"x" * 1001)


def test_github_app_token_uses_short_lived_signed_jwt(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "private-key")
    captured = {}

    def encode(payload, key, algorithm):
        captured.update(payload=payload, key=key, algorithm=algorithm)
        return "signed-jwt"

    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(encode=encode))

    def handler(request):
        assert request.headers["authorization"] == "Bearer signed-jwt"
        assert request.url.path == "/app/installations/42/access_tokens"
        return httpx.Response(201, json={"token": "installation-token"})

    provider = GitHubAppTokenProvider(
        config(tmp_path).github_app,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.token(42) == "installation-token"
    assert captured["algorithm"] == "RS256"
    assert captured["key"] == "private-key"
    assert 500 <= captured["payload"]["exp"] - captured["payload"]["iat"] <= 700


def test_github_app_token_reads_private_key_from_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    key_file = tmp_path / "github-app.pem"
    key_file.write_text("private-key-from-file\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_FILE", str(key_file))
    captured = {}

    def encode(payload, key, algorithm):
        captured.update(payload=payload, key=key, algorithm=algorithm)
        return "signed-jwt"

    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(encode=encode))

    def handler(request):
        return httpx.Response(201, json={"token": "installation-token"})

    provider = GitHubAppTokenProvider(
        config(tmp_path).github_app,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.token(42) == "installation-token"
    assert captured["key"] == "private-key-from-file"


def test_github_app_token_rejects_missing_private_key_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_FILE", str(tmp_path / "missing.pem"))
    provider = GitHubAppTokenProvider(config(tmp_path).github_app)

    with pytest.raises(ValueError, match="does not exist"):
        provider.token(42)


def test_comment_publisher_creates_once_and_rejects_ambiguous_state():
    class Auth:
        def token(self, _installation_id):
            return "token"

    calls = []

    def create_handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 11})

    publisher = GitHubCommentPublisher(
        Auth(), client=httpx.Client(transport=httpx.MockTransport(create_handler))
    )
    job = {"repository": "saleor/storefront", "pull_request": 1199, "installation_id": 42}
    assert publisher.publish(job, "report") == {"status": "CREATED", "comment_id": 11}
    assert calls == ["GET", "POST"]
    with pytest.raises(ValueError, match="too large"):
        publisher.publish(job, "x" * 60_001)

    def ambiguous_handler(_request):
        return httpx.Response(
            200,
            json=[
                {"id": 1, "body": GitHubCommentPublisher.marker},
                {"id": 2, "body": GitHubCommentPublisher.marker},
            ],
        )

    ambiguous = GitHubCommentPublisher(
        Auth(), client=httpx.Client(transport=httpx.MockTransport(ambiguous_handler))
    )
    with pytest.raises(RuntimeError, match="Multiple"):
        ambiguous.publish(job, "report")


def test_worker_completes_current_job_without_publishing(monkeypatch, tmp_path):
    selected = config(tmp_path)
    config_file = tmp_path / "webhook.json"
    config_file.write_text(selected.model_dump_json(), encoding="utf-8")
    service = WebhookService(selected, secret="secret")
    headers, payload = delivery("secret")
    service.accept(headers, payload)

    class Auth:
        def token(self, _installation_id):
            return "token"

    def handler(_request):
        return httpx.Response(
            200,
            json={"number": 1199, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}},
        )

    class Coordinator:
        def run(self, request, run_id):
            assert request.project_id == "saleor-storefront"
            assert run_id.startswith("gh-")
            return {"status": "COMPLETED", "findings": [], "gaps": [], "tool_usage": []}

    @contextmanager
    def fake_coordinator(_path):
        yield Coordinator()

    monkeypatch.setattr("trace_coordinator.bootstrap.create_coordinator", fake_coordinator)
    result = run_next_job(
        config_file,
        auth=Auth(),
        github_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result["status"] == "COMPLETED"
    assert (tmp_path / "output" / result["run_id"] / "report.json").exists()
