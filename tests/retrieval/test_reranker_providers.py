"""Configurable credentials and real SDK requests against offline mock transports."""

import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from tests.ingestion.test_providers import mock_google
from tests.retrieval.test_retrieval import SCOPE, FixedRetriever, passage
from trace_impact import Settings, create_pipeline
from trace_impact.retrieval import RetrievalConfig, StageConfig, build_retrieval_service
from trace_impact.retrieval.config import CompatibleLLMOptions, GeminiRerankerOptions
from trace_impact.retrieval.rerankers.compatible import build_compatible_reranker
from trace_impact.retrieval.rerankers.credentials import resolve_api_key
from trace_impact.shared.errors import ConfigurationError, ProviderError


def options(**updates):
    return CompatibleLLMOptions(
        model="economy-model",
        base_url="https://rerank.example.test/v1",
        max_retries=0,
        max_output_tokens=1024,
        **updates,
    )


def test_explicit_credentials_never_fall_back_or_enter_config(monkeypatch):
    monkeypatch.delenv("ENCODER_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="ENCODER_API_KEY"):
        resolve_api_key("ENCODER_API_KEY", SecretStr("different-provider-key"))
    with pytest.raises(ConfigurationError, match="ENCODER_API_KEY"):
        build_compatible_reranker(options())
    assert resolve_api_key(None, SecretStr("old-key")).get_secret_value() == "old-key"
    monkeypatch.setenv("ENCODER_API_KEY", "separate-key")
    assert resolve_api_key("ENCODER_API_KEY").get_secret_value() == "separate-key"
    assert "separate-key" not in options().model_dump_json()
    with pytest.raises(ValidationError):
        options(api_key="never-inline")


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/v1",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?api_key=secret",
        "https://example.com/#secret",
        "file:///tmp/model",
        "",
    ],
)
def test_invalid_endpoints_rejected(url):
    with pytest.raises(ValidationError):
        GeminiRerankerOptions(model="test", base_url=url)


def test_loopback_endpoint_and_environment_name_validation():
    assert GeminiRerankerOptions(model="test", base_url="http://127.0.0.1:8080/v1").base_url
    assert GeminiRerankerOptions(model="test", thinking_level=None).thinking_level is None
    with pytest.raises(ValidationError):
        options(api_key_env="contains-a-key-not-an-env-name")


def mock_compatible(monkeypatch, handler):
    real_client = httpx.Client
    clients = []

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(handler))
            clients.append(self)

    monkeypatch.setattr(httpx, "Client", Client)
    return clients


def completion(finish="stop", content=None):
    return httpx.Response(
        200,
        json={
            "id": "offline-response",
            "object": "chat.completion",
            "created": 0,
            "model": "economy-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {
                        "role": "assistant",
                        "content": content
                        or json.dumps(
                            {
                                "items": [
                                    {"id": "p1", "grade": 3, "quote": passage().text},
                                    {"id": "p2", "grade": 0, "quote": ""},
                                ]
                            }
                        ),
                    },
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        },
    )


@pytest.mark.parametrize("method", ["json_schema", "json_mode"])
def test_compatible_sdk_batches_candidates_routes_key_and_closes(monkeypatch, method):
    monkeypatch.setenv("ENCODER_API_KEY", "separate-key")
    calls = []

    def handler(request):
        assert str(request.url) == "https://rerank.example.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer separate-key"
        body = json.loads(request.content)
        calls.append(body)
        assert body["model"] == "economy-model"
        assert body.get("max_completion_tokens", body.get("max_tokens")) == 1024
        assert body["response_format"]["type"] == ("json_object" if method == "json_mode" else "json_schema")
        payload = json.loads(body["messages"][1]["content"])
        assert [p["id"] for p in payload["passages"]] == ["p1", "p2"]
        return completion()

    clients = mock_compatible(monkeypatch, handler)
    with create_pipeline(Settings(gemini_api_key="ingestion-key")) as app:
        config = RetrievalConfig(
            reranker=StageConfig(
                provider="openai_compatible", options=options(structured_output=method).model_dump()
            ),
            selector=StageConfig(
                provider="score_threshold", options={"score_kind": "llm-relevance-v1", "min_score": 3}
            ),
        )
        service = build_retrieval_service(
            FixedRetriever(passage(), passage("p2", text="Other topic")), config, app.components
        )
        result = service.run("query", SCOPE)
        assert result.selection.ids == ("p1",)
        assert result.ranking.usage["total_tokens"] == 60
        assert len(calls) == 1
        assert "separate-key" not in result.model_dump_json()
    assert all(c.is_closed for c in clients)


@pytest.mark.parametrize("failure", ["timeout", "length", "malformed", "refusal"])
def test_compatible_failure_is_not_empty_success(monkeypatch, failure):
    monkeypatch.setenv("ENCODER_API_KEY", "separate-key")

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private request detail")
        if failure == "malformed":
            return completion(content="invalid JSON")
        if failure == "refusal":
            response = completion().json()
            response["choices"][0]["message"]["refusal"] = "refused"
            return httpx.Response(200, json=response)
        return completion(finish="length")

    clients = mock_compatible(monkeypatch, handler)
    ranker = build_compatible_reranker(options())
    try:
        with pytest.raises((ProviderError, ValueError)):
            ranker.rerank("query", (passage(), passage("p2")))
    finally:
        ranker.close()
    assert all(c.is_closed for c in clients)


def test_gemini_separate_key_and_endpoint_with_real_sdk(monkeypatch):
    monkeypatch.setenv("ENCODER_API_KEY", "separate-google-key")
    calls = []

    def handler(request):
        assert request.url.host == "gemini.example.test"
        assert request.headers["x-goog-api-key"] == "separate-google-key"
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "items": [
                                                {"id": "p1", "grade": 3, "quote": passage().text},
                                            ]
                                        }
                                    )
                                }
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    clients, _ = mock_google(monkeypatch, handler)
    with create_pipeline(Settings(gemini_api_key="ingestion-only-key")) as app:
        config = RetrievalConfig(
            reranker=StageConfig(
                provider="gemini",
                options={
                    "model": "gemini-3.5-flash-lite",
                    "api_key_env": "ENCODER_API_KEY",
                    "base_url": "https://gemini.example.test",
                    "thinking_level": None,
                    "max_retries": 0,
                },
            )
        )
        result = build_retrieval_service(FixedRetriever(passage()), config, app.components).run("q", SCOPE)
        assert result.selection.ids == ("p1",)
    assert len(calls) == 1 and all(c.is_closed for c in clients)
