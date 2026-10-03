"""Authenticated GitHub webhook intake, durable jobs and idempotent PR reporting."""

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import TypeAdapter

from trace_coordinator.domain.contracts import AnalysisReportPayload, JsonObject, as_json_object
from trace_coordinator.domain.models import AnalysisRequest
from trace_coordinator.github_integration.config import GitHubWebhookConfig, load_github_webhook_config
from trace_coordinator.github_integration.interface import (
    AccessTokenProvider,
    ClaimedJob,
    PullRequestCommentPublisher,
    WebhookJob,
)
from trace_coordinator.response_formatter.interface import ResponseFormatter

_REPORT_ADAPTER = TypeAdapter(AnalysisReportPayload)


class WebhookRejected(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class WebhookStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS deliveries(
                    id TEXT PRIMARY KEY, received REAL NOT NULL, payload_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs(
                    delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),
                    repository TEXT NOT NULL, pull_request INTEGER NOT NULL,
                    base_sha TEXT NOT NULL, head_sha TEXT NOT NULL, installation_id INTEGER NOT NULL,
                    action TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL,
                    run_id TEXT NOT NULL UNIQUE, result_path TEXT, error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_pr ON jobs(repository, pull_request, updated);
                """
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            yield database
        finally:
            database.close()

    def enqueue(self, job: WebhookJob) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT payload_sha256 FROM deliveries WHERE id=?", (job["delivery_id"],)
            ).fetchone()
            if existing:
                if existing[0] != job["payload_sha256"]:
                    raise WebhookRejected("Delivery ID was reused with different content", 409)
                db.rollback()
                return False
            now = time.time()
            db.execute(
                "INSERT INTO deliveries(id,received,payload_sha256) VALUES(?,?,?)",
                (job["delivery_id"], now, job["payload_sha256"]),
            )
            db.execute(
                "UPDATE jobs SET status='SUPERSEDED',updated=? WHERE repository=? AND pull_request=? AND status IN ('QUEUED','RUNNING') AND head_sha<>?",
                (now, job["repository"], job["pull_request"], job["head_sha"]),
            )
            db.execute(
                "INSERT INTO jobs(delivery_id,repository,pull_request,base_sha,head_sha,installation_id,action,status,updated,run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    job["delivery_id"],
                    job["repository"],
                    job["pull_request"],
                    job["base_sha"],
                    job["head_sha"],
                    job["installation_id"],
                    job["action"],
                    "QUEUED",
                    now,
                    job["run_id"],
                ),
            )
            db.commit()
            return True

    def claim(self) -> ClaimedJob | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT delivery_id,repository,pull_request,base_sha,head_sha,installation_id,action,run_id FROM jobs WHERE status='QUEUED' ORDER BY updated LIMIT 1"
            ).fetchone()
            if not row:
                db.rollback()
                return None
            db.execute(
                "UPDATE jobs SET status='RUNNING',updated=? WHERE delivery_id=? AND status='QUEUED'",
                (time.time(), row[0]),
            )
            db.commit()
            return {
                "delivery_id": str(row[0]),
                "repository": str(row[1]),
                "pull_request": int(row[2]),
                "base_sha": str(row[3]),
                "head_sha": str(row[4]),
                "installation_id": int(row[5]),
                "action": str(row[6]),
                "run_id": str(row[7]),
            }

    def finish(
        self,
        delivery_id: str,
        status: Literal["COMPLETED", "FAILED", "SUPERSEDED"],
        *,
        result_path: str | None = None,
        error_code: str | None = None,
    ) -> None:
        if status not in {"COMPLETED", "FAILED", "SUPERSEDED"}:
            raise ValueError("Invalid terminal job status")
        with self.connect() as db:
            changed = db.execute(
                "UPDATE jobs SET status=?,updated=?,result_path=?,error_code=? WHERE delivery_id=? AND status='RUNNING'",
                (status, time.time(), result_path, error_code, delivery_id),
            ).rowcount
        if changed != 1:
            raise RuntimeError("Job was not running")


class WebhookService:
    def __init__(
        self,
        config: GitHubWebhookConfig,
        *,
        secret: str | None = None,
        store: WebhookStore | None = None,
    ) -> None:
        self.config = config
        resolved_secret = secret or os.environ.get(config.webhook_secret_env)
        if not resolved_secret:
            raise ValueError(f"Missing webhook secret: {config.webhook_secret_env}")
        self.secret: str = resolved_secret
        self.store = store or WebhookStore(config.database_file)

    def accept(self, headers: Mapping[str, str], body: bytes) -> JsonObject:
        headers = {key.casefold(): value for key, value in headers.items()}
        if len(body) > self.config.max_payload_bytes:
            raise WebhookRejected("Webhook payload exceeds the configured limit", 413)
        signature = headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            raise WebhookRejected("Invalid webhook signature", 403)
        delivery = headers.get("x-github-delivery", "")
        if not re.fullmatch(r"[A-Za-z0-9-]{8,100}", delivery):
            raise WebhookRejected("Missing or invalid GitHub delivery ID")
        if headers.get("x-github-event") != "pull_request":
            return {"status": "IGNORED", "reason": "unsupported event", "delivery_id": delivery}
        try:
            payload = json.loads(body)
            action = payload["action"]
            repository = payload["repository"]["full_name"]
            pull_request = int(payload["number"])
            base_sha = payload["pull_request"]["base"]["sha"]
            head_sha = payload["pull_request"]["head"]["sha"]
            installation_id = int(payload["installation"]["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WebhookRejected("Malformed pull_request webhook") from exc
        if action not in self.config.accepted_actions:
            return {"status": "IGNORED", "reason": "unsupported action", "delivery_id": delivery}
        allowed = {item.casefold() for item in self.config.allowed_repositories}
        if repository.casefold() not in allowed:
            raise WebhookRejected("Repository is not allowed", 403)
        if not re.fullmatch(r"[a-f0-9]{40}", base_sha) or not re.fullmatch(r"[a-f0-9]{40}", head_sha):
            raise WebhookRejected("Webhook contains invalid commit identities")
        job: WebhookJob = {
            "delivery_id": delivery,
            "payload_sha256": hashlib.sha256(body).hexdigest(),
            "repository": repository,
            "pull_request": pull_request,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "installation_id": installation_id,
            "action": action,
            "run_id": f"gh-{delivery}"[:80],
        }
        queued = self.store.enqueue(job)
        return {
            "status": "QUEUED" if queued else "DUPLICATE",
            "delivery_id": delivery,
            "run_id": job["run_id"],
        }


def create_webhook_app(config_path: Path) -> Any:
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        raise ValueError("Install the coordinator 'webhook' extra to serve HTTP") from exc
    config = load_github_webhook_config(config_path)
    service = WebhookService(config)
    app = FastAPI(title="Trace Impact GitHub webhook")

    @app.post("/github/webhook")
    async def receive(request: Request) -> Any:
        body = await request.body()
        try:
            result = service.accept(dict(request.headers), body)
        except WebhookRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return JSONResponse(result, status_code=202 if result["status"] == "QUEUED" else 200)

    return app


def run_next_job(
    config_path: str | Path,
    *,
    publisher: PullRequestCommentPublisher | None = None,
    auth: AccessTokenProvider | None = None,
    github_client: Any | None = None,
    response_formatter: ResponseFormatter | None = None,
) -> JsonObject:
    from trace_coordinator.github_integration.implementations import (
        GitHubAppTokenProvider,
        GitHubPullRequestCommentPublisher,
    )
    from trace_coordinator.setup import create_coordinator, create_response_formatter

    config = load_github_webhook_config(config_path)
    store = WebhookStore(config.database_file)
    job = store.claim()
    if not job:
        return {"status": "EMPTY"}
    auth = auth or GitHubAppTokenProvider(config.github_app)
    token = auth.token(job["installation_id"])
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": config.github_app.api_version,
    }
    client = github_client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        current = client.get(
            f"https://api.github.com/repos/{job['repository']}/pulls/{job['pull_request']}",
            headers=headers,
        )
        current.raise_for_status()
        metadata = as_json_object(current.json())
        base = as_json_object(metadata.get("base", {}))
        head = as_json_object(metadata.get("head", {}))
        if (
            metadata.get("number") != job["pull_request"]
            or base.get("sha") != job["base_sha"]
            or head.get("sha") != job["head_sha"]
        ):
            store.finish(job["delivery_id"], "SUPERSEDED", error_code="PR_REVISION_CHANGED")
            return {"status": "SUPERSEDED", "run_id": job["run_id"]}
    except Exception as exc:
        store.finish(job["delivery_id"], "FAILED", error_code=type(exc).__name__)
        raise
    finally:
        if github_client is None:
            client.close()
    request = AnalysisRequest(
        project_id=config.project_id,
        repository=job["repository"],
        pull_request=job["pull_request"],
        question=config.question,
    )
    output = Path(config.output_directory) / job["run_id"]
    output.mkdir(parents=True, exist_ok=True)
    try:
        with create_coordinator(Path(config.coordinator_config_file)) as coordinator:
            report = _REPORT_ADAPTER.validate_python(coordinator.run(request, job["run_id"]))
        report_path = output / "report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if response_formatter is None:
            with create_response_formatter(Path(config.coordinator_config_file)) as formatter:
                rendered = formatter.format(report)
        else:
            rendered = response_formatter.format(report)
        (output / "report.md").write_text(rendered, encoding="utf-8")
        if config.publish == "comment":
            if publisher is None:
                publisher = GitHubPullRequestCommentPublisher(auth, config.github_app.api_version)
            publisher.publish(job, rendered)
        store.finish(job["delivery_id"], "COMPLETED", result_path=str(report_path))
        return {"status": "COMPLETED", "run_id": job["run_id"], "report": str(report_path)}
    except Exception as exc:
        store.finish(job["delivery_id"], "FAILED", error_code=type(exc).__name__)
        raise
