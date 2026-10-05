import json
from pathlib import Path

import httpx
import pytest

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import ChangedFile, PullRequestRef, PullRequestSnapshot
from impact_agent.model.implementations.gemini_graph_query_planner import (
    GeminiGraphQueryPlanner,
    GraphQueryPlanningError,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def post(self, url, **kwargs):
        self.request = (url, kwargs)
        return FakeResponse(self.payload)


def _settings():
    return JsonConfigLoader().load(Path(__file__).parents[1] / "config" / "default")


def _pull_request():
    return PullRequestSnapshot(
        PullRequestRef("owner/storefront", 9),
        "Change cart behavior",
        "PR text",
        "base",
        "head",
        (ChangedFile("src/cart.ts", "modified", 1, 0),),
        "diff",
    )


def test_gemini_planner_sends_schema_and_pr_and_returns_structured_cypher():
    query = (
        "MATCH (n) RETURN n.path AS changed_path, [] AS related_files, "
        "[] AS related_symbols, [] AS confirmed_ui_mappings LIMIT 10"
    )
    client = FakeClient(
        {"candidates": [{"content": {"parts": [{"text": json.dumps({"cypher": query})}]}}]}
    )
    planner = GeminiGraphQueryPlanner(_settings().models, "test-key", 5, client=client)

    result = planner.create_query(_pull_request(), '{"nodes":["CodeFile"]}', "sha", 10, 3)

    assert result == query
    url, request = client.request
    assert "gemini-3.8-flash" in url
    assert request["headers"]["x-goog-api-key"] == "test-key"
    prompt = request["json"]["contents"][0]["parts"][0]["text"]
    assert json.loads(prompt)["graph_schema"] == '{"nodes":["CodeFile"]}'
    assert json.loads(prompt)["maximum_call_depth"] == 3
    assert "src/cart.ts" in prompt
    assert "sha" in prompt
    assert "at most 10" in request["json"]["system_instruction"]["parts"][0]["text"]
    prompt_text = request["json"]["system_instruction"]["parts"][0]["text"]
    assert "maximum_call_depth" in prompt_text
    assert "up to 3 hops" in prompt_text
    assert "IMPORTS-only chain" in prompt_text
    assert "one row per changed file" in prompt_text
    assert "split(coalesce(ui.query_parameter_names, ''), ',')" in prompt_text


def test_gemini_planner_rejects_malformed_structured_response():
    client = FakeClient({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]})
    planner = GeminiGraphQueryPlanner(_settings().models, "test-key", 5, client=client)

    with pytest.raises(GraphQueryPlanningError, match="invalid graph query plan"):
        planner.create_query(_pull_request(), "{}", "sha", 10, 3)


def test_gemini_planner_wraps_network_errors():
    class FailedClient:
        def post(self, *_args, **_kwargs):
            raise httpx.ConnectError("offline")

    planner = GeminiGraphQueryPlanner(_settings().models, "test-key", 5, client=FailedClient())

    with pytest.raises(GraphQueryPlanningError, match="request failed"):
        planner.create_query(_pull_request(), "{}", "sha", 10, 3)
