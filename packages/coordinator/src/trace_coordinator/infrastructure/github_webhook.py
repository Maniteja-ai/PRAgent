"""Authenticated GitHub webhook intake, durable jobs and idempotent PR reporting."""

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, field_validator

from trace_coordinator.domain.models import AnalysisRequest, Record


class GitHubAppAuth(Record):
    app_id_env: str = Field(default="GITHUB_APP_ID", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    private_key_env: str = Field(default="GITHUB_APP_PRIVATE_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    private_key_file_env: str = Field(
        default="GITHUB_APP_PRIVATE_KEY_FILE", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    api_version: str = Field(default="2026-03-10", pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")


class WebhookConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    webhook_secret_env: str = Field(default="GITHUB_WEBHOOK_SECRET", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    allowed_repositories: tuple[str, ...] = Field(min_length=1, max_length=100)
    accepted_actions: tuple[Literal["opened", "reopened", "synchronize", "ready_for_review"], ...] = (
        "opened",
        "reopened",
        "synchronize",
        "ready_for_review",
    )
    database_file: str
    coordinator_config_file: str
    output_directory: str
    project_id: str = Field(min_length=1, max_length=200)
    question: str = Field(default="Which UI flows and requirements could this PR affect?", max_length=4000)
    publish: Literal["disabled", "comment"] = "disabled"
    github_app: GitHubAppAuth = Field(default_factory=GitHubAppAuth)
    max_payload_bytes: int = Field(default=1_000_000, ge=1000, le=10_000_000)

    @field_validator("allowed_repositories")
    @classmethod
    def valid_repositories(cls, values):
        pattern = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
        if len(set(value.casefold() for value in values)) != len(values) or any(
            not pattern.fullmatch(value) for value in values
        ):
            raise ValueError("Repositories must be unique owner/name values")
        return values


def load_webhook_config(path):
    path = Path(path).resolve()
    config = WebhookConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    return config.model_copy(
        update={
            name: str((path.parent / getattr(config, name)).resolve())
            for name in ("database_file", "coordinator_config_file", "output_directory")
        }
    )


class WebhookRejected(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class WebhookStore:
    def __init__(self, path):
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
    def connect(self):
        database = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            yield database
        finally:
            database.close()

    def enqueue(self, job):
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

    def claim(self):
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
            keys = (
                "delivery_id",
                "repository",
                "pull_request",
                "base_sha",
                "head_sha",
                "installation_id",
                "action",
                "run_id",
            )
            return dict(zip(keys, row, strict=True))

    def finish(self, delivery_id, status, *, result_path=None, error_code=None):
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
    def __init__(self, config, *, secret=None, store=None):
        self.config = config
        self.secret = secret or os.environ.get(config.webhook_secret_env)
        if not self.secret:
            raise ValueError(f"Missing webhook secret: {config.webhook_secret_env}")
        self.store = store or WebhookStore(config.database_file)

    def accept(self, headers, body):
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
        job = {
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


class GitHubAppTokenProvider:
    def __init__(self, config, client=None):
        self.config = config
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def token(self, installation_id):
        app_id = os.environ.get(self.config.app_id_env)
        private_key = self._private_key()
        if not app_id or not private_key:
            raise ValueError("GitHub App credentials are unavailable")
        try:
            import jwt
        except ImportError as exc:
            raise ValueError("Install the coordinator 'webhook' extra for GitHub App auth") from exc
        now = int(time.time())
        signed = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256"
        )
        response = self.client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {signed}",
                "X-GitHub-Api-Version": self.config.api_version,
            },
            json={"permissions": {"issues": "write", "pull_requests": "write", "contents": "read"}},
        )
        response.raise_for_status()
        return response.json()["token"]

    def _private_key(self):
        inline_key = os.environ.get(self.config.private_key_env)
        if inline_key:
            return inline_key
        key_file = os.environ.get(self.config.private_key_file_env)
        if not key_file:
            return None
        path = Path(key_file).expanduser()
        if not path.is_file():
            raise ValueError(f"GitHub App private key file does not exist: {path}")
        private_key = path.read_text(encoding="utf-8").strip()
        if not private_key:
            raise ValueError(f"GitHub App private key file is empty: {path}")
        return private_key


class GitHubCommentPublisher:
    marker = "<!-- trace-impact-report -->"

    def __init__(self, auth, api_version="2026-03-10", client=None):
        self.auth, self.api_version = auth, api_version
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def publish(self, job, body):
        if len(body) > 60_000:
            raise ValueError("PR report is too large for a comment")
        token = self.auth.token(job["installation_id"])
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": self.api_version,
        }
        root = f"https://api.github.com/repos/{job['repository']}"
        response = self.client.get(
            f"{root}/issues/{job['pull_request']}/comments", headers=headers, params={"per_page": 100}
        )
        response.raise_for_status()
        matches = [item for item in response.json() if self.marker in item.get("body", "")]
        if len(matches) > 1:
            raise RuntimeError("Multiple Trace Impact comments exist; refusing an ambiguous update")
        value = self.marker + "\n" + body
        if matches:
            result = self.client.patch(
                f"{root}/issues/comments/{matches[0]['id']}", headers=headers, json={"body": value}
            )
            operation = "updated"
        else:
            result = self.client.post(
                f"{root}/issues/{job['pull_request']}/comments", headers=headers, json={"body": value}
            )
            operation = "created"
        result.raise_for_status()
        return {"status": operation.upper(), "comment_id": result.json()["id"]}


def create_webhook_app(config_path):
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        raise ValueError("Install the coordinator 'webhook' extra to serve HTTP") from exc
    config = load_webhook_config(config_path)
    service = WebhookService(config)
    app = FastAPI(title="Trace Impact GitHub webhook")

    @app.post("/github/webhook")
    async def receive(request: Request):
        body = await request.body()
        try:
            result = service.accept(dict(request.headers), body)
        except WebhookRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return JSONResponse(result, status_code=202 if result["status"] == "QUEUED" else 200)

    return app


def run_next_job(config_path, *, publisher=None, auth=None, github_client=None):
    from trace_coordinator.bootstrap import create_coordinator
    from trace_coordinator.presentation.report_formatter import markdown

    config = load_webhook_config(config_path)
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
        metadata = current.json()
        if (
            metadata.get("number") != job["pull_request"]
            or metadata.get("base", {}).get("sha") != job["base_sha"]
            or metadata.get("head", {}).get("sha") != job["head_sha"]
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
            report = coordinator.run(request, job["run_id"])
        report_path = output / "report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        rendered = markdown(report)
        (output / "report.md").write_text(rendered, encoding="utf-8")
        if config.publish == "comment":
            if publisher is None:
                publisher = GitHubCommentPublisher(auth, config.github_app.api_version)
            publisher.publish(job, rendered)
        store.finish(job["delivery_id"], "COMPLETED", result_path=str(report_path))
        return {"status": "COMPLETED", "run_id": job["run_id"], "report": str(report_path)}
    except Exception as exc:
        store.finish(job["delivery_id"], "FAILED", error_code=type(exc).__name__)
        raise


def webhook_schema():
    return {
        **WebhookConfig.model_json_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "GitHub webhook integration",
    }
