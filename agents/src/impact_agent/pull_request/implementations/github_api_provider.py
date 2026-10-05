"""Read pull request metadata and complete changed-file patches from GitHub."""

from collections.abc import Mapping
from typing import Literal, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from impact_agent.config.validation.github import GitHubConfig
from impact_agent.domain.models import ChangedFile, PullRequestRef, PullRequestSnapshot
from impact_agent.pull_request.interface.pull_request_provider import PullRequestProvider


class GitHubPullRequestError(RuntimeError):
    """A GitHub request failed or returned data that cannot be safely analyzed."""


class _GitHubBranch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sha: str = Field(min_length=1)


class _GitHubPullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    body: str | None = None
    base: _GitHubBranch
    head: _GitHubBranch


class _GitHubFile(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filename: str = Field(min_length=1)
    status: Literal["added", "modified", "removed", "renamed"]
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    patch: str | None = None


_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


class GitHubApiPullRequestProvider(PullRequestProvider):
    """Typed, bounded GitHub REST adapter. Inject a client to test without network access."""

    def __init__(
        self,
        config: GitHubConfig,
        token: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("GitHub token cannot be empty")
        self._config = config
        self._owns_client = client is None
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

    def fetch(self, reference: PullRequestRef) -> PullRequestSnapshot:
        owner, repository = self._parse_repository(reference.repository)
        repository_path = f"repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        request_path = f"{repository_path}/pulls/{reference.number}"
        pull_request = self._get_model(request_path, _GitHubPullRequest)
        changed_files = self._fetch_files(f"{request_path}/files")
        diff = self._create_diff(changed_files)
        return PullRequestSnapshot(
            reference=reference,
            title=pull_request.title,
            description=pull_request.body or "",
            base_sha=pull_request.base.sha,
            head_sha=pull_request.head.sha,
            files=changed_files,
            diff=diff,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _parse_repository(repository: str) -> tuple[str, str]:
        parts = repository.split("/")
        if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
            raise ValueError("Repository must be written as owner/name")
        return parts[0], parts[1]

    def _get_model(self, path: str, model: type[_ResponseModel]) -> _ResponseModel:
        response = self._request(path)
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise GitHubPullRequestError(
                "GitHub returned an invalid pull request response"
            ) from error

    def _fetch_files(self, path: str) -> tuple[ChangedFile, ...]:
        files: list[ChangedFile] = []
        page = 1
        while True:
            response = self._request(path, params={"per_page": 100, "page": page})
            try:
                page_files = [_GitHubFile.model_validate(item) for item in response.json()]
            except (TypeError, ValueError, ValidationError) as error:
                raise GitHubPullRequestError(
                    "GitHub returned an invalid changed-file response"
                ) from error
            files.extend(
                ChangedFile(
                    path=file.filename,
                    change_type=file.status,
                    additions=file.additions,
                    deletions=file.deletions,
                    patch=file.patch,
                )
                for file in page_files
            )
            if len(files) > self._config.max_changed_files:
                raise GitHubPullRequestError(
                    "Pull request exceeds the configured changed-file limit"
                )
            if len(page_files) < 100:
                break
            page += 1
        return tuple(files)

    def _request(self, path: str, params: dict[str, int] | None = None) -> httpx.Response:
        try:
            url = f"{self._config.api_url.rstrip('/')}/{path.lstrip('/')}"
            response = self._client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            raise GitHubPullRequestError(
                f"GitHub API returned HTTP {error.response.status_code} for pull request data"
            ) from error
        except httpx.RequestError as error:
            raise GitHubPullRequestError("GitHub API request failed") from error

    def _create_diff(self, files: tuple[ChangedFile, ...]) -> str:
        sections = [
            f"diff --git a/{file.path} b/{file.path}\n{file.patch or '[patch unavailable]'}"
            for file in files
        ]
        diff = "\n".join(sections)
        if len(diff.encode("utf-8")) > self._config.max_diff_bytes:
            raise GitHubPullRequestError("Pull request diff exceeds the configured size limit")
        return diff


class GitHubPullRequestProviderFactory:
    """Create the configured GitHub adapter without exposing token handling to callers."""

    @staticmethod
    def create(
        config: GitHubConfig, environment: Mapping[str, str]
    ) -> GitHubApiPullRequestProvider:
        token = environment.get(config.token_env)
        if not token:
            raise ValueError(
                f"Required GitHub token environment variable is missing: {config.token_env}"
            )
        return GitHubApiPullRequestProvider(config, token)
