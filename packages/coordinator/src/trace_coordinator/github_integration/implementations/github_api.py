"""GitHub App authentication and pull-request comment publishing."""

import os
import time
from pathlib import Path
from typing import Any

import httpx

from trace_coordinator.domain.contracts import JsonObject, as_json_object
from trace_coordinator.github_integration.config import GitHubAppCredentials
from trace_coordinator.github_integration.interface import AccessTokenProvider, ClaimedJob


class GitHubAppTokenProvider:
    def __init__(self, config: GitHubAppCredentials, client: Any | None = None) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def token(self, installation_id: int) -> str:
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
        token = as_json_object(response.json()).get("token")
        if not isinstance(token, str):
            raise ValueError("GitHub App token response is invalid")
        return token

    def _private_key(self) -> str | None:
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


class GitHubPullRequestCommentPublisher:
    marker = "<!-- trace-impact-report -->"

    def __init__(
        self,
        auth: AccessTokenProvider,
        api_version: str = "2026-03-10",
        client: Any | None = None,
    ) -> None:
        self.auth = auth
        self.api_version = api_version
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)

    def publish(self, job: ClaimedJob, body: str) -> JsonObject:
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
        response_payload = as_json_object(result.json())
        return {"status": operation.upper(), "comment_id": response_payload["id"]}
