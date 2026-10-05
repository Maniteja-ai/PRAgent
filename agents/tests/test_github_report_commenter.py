import json
from pathlib import Path

import httpx

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import AgentReport, PullRequestRef, ReportStatus
from impact_agent.pull_request.implementations.github_report_commenter import (
    GitHubReportCommenter,
)


def _report() -> AgentReport:
    return AgentReport(
        run_id="test-run",
        status=ReportStatus.COMPLETED,
        summary="The cart update flow may be affected.",
        findings=(),
        evidence=(),
        behavior_results=(),
        gaps=(),
        rendered_report="# PR impact analysis\n\nCart update flow may be affected.",
    )


def _config():
    return JsonConfigLoader().load(Path(__file__).parents[1] / "config" / "default").github


def test_publisher_creates_a_comment_when_no_previous_agent_comment_exists():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200 if request.method == "GET" else 201,
            json=[] if request.method == "GET" else {"id": 1},
        )

    client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(respond)
    )
    publisher = GitHubReportCommenter(_config(), "token", client=client)
    publisher.publish(PullRequestRef("owner/repo", 7), _report())

    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[-1].url.path == "/repos/owner/repo/issues/7/comments"
    assert "<!-- pragent-impact-report -->" in json.loads(requests[-1].content)["body"]
    client.close()


def test_publisher_updates_existing_agent_comment_instead_of_adding_duplicates():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200, json=[{"id": 91, "body": "<!-- pragent-impact-report --> old"}]
            )
        return httpx.Response(200, json={"id": 91})

    client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(respond)
    )
    publisher = GitHubReportCommenter(_config(), "token", client=client)
    publisher.publish(PullRequestRef("owner/repo", 7), _report())

    assert [request.method for request in requests] == ["GET", "PATCH"]
    assert requests[-1].url.path == "/repos/owner/repo/issues/comments/91"
    client.close()
