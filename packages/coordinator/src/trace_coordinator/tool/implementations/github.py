"""Read public GitHub metadata and compute complete diffs from immutable Git objects."""

import os
import re
from pathlib import Path

import httpx
from pydantic import BaseModel

from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import ToolContext, ToolResult
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.ledger import digest
from trace_coordinator.tool.dependencies.git_changes import compare, evidence
from trace_coordinator.tool.implementations.fixture import PRInput


class GitHubDiffTool:
    name = "github.diff"
    description = "Read pinned PR changes and verify the deployed comparison has the same patch."
    input_model = PRInput
    allowed_agents = frozenset({"coordinator"})

    def __init__(
        self,
        application: ApplicationConfig,
        artifact_root: Path,
        client: httpx.Client | None = None,
    ) -> None:
        self.app, self.artifact_root = application, artifact_root
        self.version = "github-git-v2:" + digest(application.model_dump(mode="json"))
        token = os.environ.get(application.github_token_env) if application.github_token_env else None
        if application.github_token_env and not token:
            raise ValueError("Configured GitHub token environment variable is missing")
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = client or httpx.Client(
            headers=headers, timeout=application.github_timeout_seconds, follow_redirects=False
        )

    def close(self) -> None:
        self.client.close()

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolResult:
        arguments = PRInput.model_validate(arguments)
        if arguments.repository != self.app.repository or context.project_id != self.app.project_id:
            raise ToolFailure("PR repository/project does not match the application")
        response = self.client.get(
            f"https://api.github.com/repos/{arguments.repository}/pulls/{arguments.pull_request}"
        )
        if response.status_code != 200:
            raise ToolFailure(
                "GitHub metadata unavailable", retryable=response.status_code in (429, 500, 502, 503, 504)
            )
        pr = response.json()
        if (
            pr["base"]["repo"]["full_name"].casefold() != arguments.repository.casefold()
            or pr["number"] != arguments.pull_request
        ):
            raise ToolFailure("GitHub returned metadata for a different PR")
        base, head = pr["base"]["sha"], pr["head"]["sha"]
        if not all(re.fullmatch(r"[a-f0-9]{40}", sha) for sha in (base, head)):
            raise ToolFailure("GitHub returned invalid commit identities")
        comparison = compare(self.app, base, head)
        # Force-push protection: metadata must still name the same head after reading Git objects.
        again = self.client.get(
            f"https://api.github.com/repos/{arguments.repository}/pulls/{arguments.pull_request}"
        )
        if (
            again.status_code != 200
            or again.json()["head"]["sha"] != head
            or again.json()["base"]["sha"] != base
        ):
            raise ToolFailure("PR changed during analysis; start a new analysis")
        return evidence(
            self.app,
            self.artifact_root,
            arguments,
            context,
            comparison,
            title=pr["title"],
            source=pr["html_url"],
            historical_replay=bool(pr.get("merged")),
            provider="github",
        )
