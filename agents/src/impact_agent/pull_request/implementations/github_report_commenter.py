"""Publish or update a single PRAgent summary comment on a GitHub pull request."""

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from impact_agent.config.validation.github import GitHubConfig
from impact_agent.domain.models import AgentReport, PullRequestRef
from impact_agent.pull_request.interface.report_commenter import ReportCommenter

_COMMENT_MARKER = "<!-- pragent-impact-report -->"


class GitHubReportCommenter(ReportCommenter):
    def __init__(
        self, config: GitHubConfig, token: str, *, client: httpx.Client | None = None
    ) -> None:
        if not token.strip():
            raise ValueError("GitHub token cannot be empty")
        self._config = config
        self._client = client or httpx.Client(
            base_url=config.api_url.rstrip("/"),
            timeout=config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pragent",
            },
        )

    def publish(self, reference: PullRequestRef, report: AgentReport) -> None:
        owner, repository = self._repository_parts(reference.repository)
        path = (
            f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
            f"/issues/{reference.number}/comments"
        )
        body = f"{_COMMENT_MARKER}\n\n{report.rendered_report}"
        comments = self._request("GET", path, params={"per_page": 100}).json()
        if not isinstance(comments, list):
            raise RuntimeError("GitHub returned an invalid pull request comments response")
        existing = next(
            (
                item
                for item in reversed(comments)
                if isinstance(item, dict)
                and _COMMENT_MARKER in str(item.get("body", ""))
                and isinstance(item.get("id"), int)
            ),
            None,
        )
        if existing is None:
            self._request("POST", path, json={"body": body})
        else:
            comment_path = (
                f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
                f"/issues/comments/{existing['id']}"
            )
            self._request("PATCH", comment_path, json={"body": body})

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _repository_parts(repository: str) -> tuple[str, str]:
        parts = repository.split("/")
        if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
            raise ValueError("Repository must be written as owner/name")
        return parts[0], parts[1]

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"GitHub report comment request failed with HTTP {error.response.status_code}"
            ) from error
        except httpx.RequestError as error:
            raise RuntimeError("GitHub report comment request failed") from error


class GitHubReportCommenterFactory:
    @staticmethod
    def create(
        config: GitHubConfig, environment: Mapping[str, str]
    ) -> GitHubReportCommenter | None:
        if not config.publish_report_comment:
            return None
        token = environment.get(config.token_env)
        if not token:
            raise ValueError(
                f"Required GitHub token environment variable is missing: {config.token_env}"
            )
        return GitHubReportCommenter(config, token)
