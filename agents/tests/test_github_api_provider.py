from pathlib import Path

import httpx
import pytest

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import PullRequestRef
from impact_agent.pull_request.implementations.github_api_provider import (
    GitHubApiPullRequestProvider,
    GitHubPullRequestError,
)


def create_response(request: httpx.Request, *, body: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body, request=request)


def test_github_adapter_builds_typed_pull_request_and_changed_file_snapshot():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/pulls/42"):
            body = {
                "title": "Update cart voucher flow",
                "body": "Keep displayed totals current.",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha"},
            }
        else:
            body = [
                {
                    "filename": "src/cart.ts",
                    "status": "modified",
                    "additions": 3,
                    "deletions": 1,
                    "patch": "@@ -1 +1 @@\n-old\n+new",
                }
            ]
        return create_response(request, body=body)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    config = JsonConfigLoader().load(Path(__file__).parents[1] / "config" / "default").github
    provider = GitHubApiPullRequestProvider(config, "test-token", client=client)

    result = provider.fetch(PullRequestRef("owner/storefront", 42))

    assert result.title == "Update cart voucher flow"
    assert result.base_sha == "base-sha"
    assert result.head_sha == "head-sha"
    assert result.files[0].path == "src/cart.ts"
    assert "+new" in result.diff
    assert requests[0].url.path == "/repos/owner/storefront/pulls/42"
    assert requests[1].url.params["per_page"] == "100"
    client.close()


def test_github_adapter_rejects_bad_repository_and_http_failures():
    config = JsonConfigLoader().load(Path(__file__).parents[1] / "config" / "default").github
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: create_response(request, body={"message": "Not Found"}, status=404)
        )
    )
    provider = GitHubApiPullRequestProvider(config, "test-token", client=client)

    with pytest.raises(ValueError, match="owner/name"):
        provider.fetch(PullRequestRef("https://github.com/owner/repo", 1))
    with pytest.raises(GitHubPullRequestError, match="HTTP 404"):
        provider.fetch(PullRequestRef("owner/repo", 1))
    client.close()


def test_github_adapter_fails_closed_when_diff_is_too_large():
    config = JsonConfigLoader().load(Path(__file__).parents[1] / "config" / "default").github
    limited = config.model_copy(update={"max_diff_bytes": 1_000})

    def respond(request: httpx.Request) -> httpx.Response:
        body = (
            {"title": "Title", "body": "", "base": {"sha": "b"}, "head": {"sha": "h"}}
            if request.url.path.endswith("/pulls/1")
            else [
                {
                    "filename": "large.txt",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": "+" + "x" * 1100,
                }
            ]
        )
        return create_response(request, body=body)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = GitHubApiPullRequestProvider(limited, "test-token", client=client)

    with pytest.raises(GitHubPullRequestError, match="size limit"):
        provider.fetch(PullRequestRef("owner/repo", 1))
    client.close()
