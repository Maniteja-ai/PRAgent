"""Real provider SDKs against mock HTTP; no billable requests or semantic-quality claims."""

import json
from pathlib import Path

import httpx
import pytest
from google import genai
from google.genai import types
from pydantic import ValidationError

from trace_impact import Settings, create_pipeline
from trace_impact.errors import ConfigurationError, ExtractionError, ProviderError
from trace_impact.implementations.rate_limit import RequestPacer
from trace_impact.models import EmbeddingConfig, ExtractionConfig, ExtractionRun, load_project
from trace_impact.registry import Registry

ROOT = Path(__file__).resolve().parents[1]


def project_file(tmp_path, provider="gemini"):
    project = load_project(ROOT / "projects/example/project.json")
    project.extractor = ExtractionConfig(
        provider=provider, model="gemini-3.7-flash", thinking_level="low", requests_per_minute=0
    )
    project.embedding_provider = EmbeddingConfig(
        provider=provider, model="gemini-embedding-2", dimensions=768, requests_per_minute=0
    )
    path = tmp_path / "project.json"
    path.write_text(project.model_dump_json(), encoding="utf-8")
    (tmp_path / "spec.md").write_bytes((ROOT / "projects/example/spec.md").read_bytes())
    return path


def test_json_providers_are_validated_without_credentials_or_requests(tmp_path):
    path = project_file(tmp_path)
    with create_pipeline() as pipeline:
        _, corpus = pipeline.collect(path, tmp_path / "runs")
        assert not corpus.errors
        with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
            pipeline.components.extractors.resolve(corpus.project.extractor)
    data = json.loads(path.read_text())
    data["extractor"]["provider"] = "unknown_provider"
    path.write_text(json.dumps(data))
    with create_pipeline() as pipeline:
        with pytest.raises(ConfigurationError, match="Unknown extractor.*unknown_provider"):
            pipeline.validate(path)
    data["extractor"]["api_key"] = "not-allowed-in-project"
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        load_project(path)


def test_configured_instances_are_isolated_by_full_model_configuration():
    registry = Registry("test")
    seen = []

    def build(config):
        seen.append(config.model)
        return object()

    registry.register_configured_factory("custom", build)
    a = ExtractionConfig(provider="custom", model="a")
    b = ExtractionConfig(provider="custom", model="b")
    assert registry.resolve(a) is registry.resolve(a)
    assert registry.resolve(a) is not registry.resolve(b)
    assert seen == ["a", "b"]
    registry.close()


def mock_google(monkeypatch, handler):
    from langchain_google_genai import chat_models

    from trace_impact.implementations import gemini

    real_client = genai.Client
    clients = []
    backends = []

    def factory(**kwargs):
        backends.append(kwargs.get("vertexai", False))
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        options = kwargs.get("http_options") or types.HttpOptions()
        options.httpx_client = client
        kwargs["http_options"] = options
        sdk = real_client(**kwargs)
        original_close = sdk.close

        def close():
            original_close()
            client.close()  # This test factory owns the injected HTTP transport.

        sdk.close = close
        return sdk

    monkeypatch.setattr(chat_models, "Client", factory)
    monkeypatch.setattr(gemini.genai, "Client", factory)
    return clients, backends


def extraction_response(finish="STOP"):
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
                                        "requirements": [],
                                        "no_requirement_reason": "Labeled SDK test response only",
                                    }
                                )
                            }
                        ],
                    },
                    "finishReason": finish,
                    "index": 0,
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 10, "totalTokenCount": 20},
            "modelVersion": "gemini-3.7-flash",
        },
    )


def test_gemini_json_selection_uses_structured_output_and_reuses_cache(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append((str(request.url), json.loads(request.content)))
        return extraction_response()

    clients, backends = mock_google(monkeypatch, handler)
    path = project_file(tmp_path)
    settings = Settings(gemini_api_key="test-only-key", model_retries=0)
    with create_pipeline(settings) as pipeline:
        folder, corpus = pipeline.collect(path, tmp_path / "runs")
        first = pipeline.extract(folder)
        second = pipeline.extract(folder)
        assert first.status == second.status == "COMPLETE"
        assert first.provider == "gemini" and first.model == "gemini-3.7-flash"
    assert len(requests) == len(corpus.chunks)
    assert all("gemini-3.7-flash:generateContent" in url for url, _ in requests)
    generation = requests[0][1]["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation.get("responseJsonSchema") or generation.get("responseSchema")
    thinking = generation["thinkingConfig"]
    assert thinking.get("thinkingLevel", thinking.get("thinking_level", "")).upper() == "LOW"
    assert clients and all(c.is_closed for c in clients)
    assert not any(backends)


def test_gemini_quota_failure_stops_and_preserves_partial_checkpoint(tmp_path, monkeypatch):
    requests = []
    fail = [True]

    def handler(request):
        requests.append(str(request.url))
        if fail[0]:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "message": "Synthetic quota exhaustion",
                    }
                },
            )
        return extraction_response()

    mock_google(monkeypatch, handler)
    with create_pipeline(Settings(gemini_api_key="test-only", model_retries=0)) as pipeline:
        folder, corpus = pipeline.collect(project_file(tmp_path), tmp_path / "runs")
        with pytest.raises(ProviderError, match="quota"):
            pipeline.extract(folder)
        partial = ExtractionRun.model_validate_json((folder / "extraction.json").read_text())
        assert partial.status == "PARTIAL" and len(partial.errors) == 1
        assert len(requests) == 1
        fail[0] = False
        assert pipeline.extract(folder).status == "COMPLETE"
        assert len(requests) == len(corpus.chunks) + 1


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY"])
def test_gemini_incomplete_structured_output_is_rejected(tmp_path, monkeypatch, finish):
    mock_google(monkeypatch, lambda _: extraction_response(finish))
    with create_pipeline(Settings(gemini_api_key="test-only", model_retries=0)) as pipeline:
        _, corpus = pipeline.collect(project_file(tmp_path), tmp_path / "runs")
        provider = pipeline.components.extractors.resolve(corpus.project.extractor)
        with pytest.raises(ExtractionError):
            provider.extract(corpus.chunks[0], corpus.snapshots[0], corpus.project.scope)


def test_embedding2_wire_keeps_documents_separate_and_uses_search_prefixes(monkeypatch):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        return httpx.Response(
            200, json={"embeddings": [{"values": [1.0] + [0.0] * 767} for _ in body["requests"]]}
        )

    clients, backends = mock_google(monkeypatch, handler)
    with create_pipeline(Settings(gemini_api_key="test-only", model_retries=0)) as pipeline:
        config = EmbeddingConfig(
            provider="gemini", model="gemini-embedding-2", dimensions=768, requests_per_minute=0
        )
        provider = pipeline.components.embeddings.resolve(config)
        assert len(provider.embed_documents(["first document", "second document"])) == 2
        assert len(provider.embed_query("find checkout")) == 768
        assert provider.profile.preprocessing == "gemini-embedding-2-search-v1"
    assert len(requests) == 2
    assert all("gemini-embedding-2:batchEmbedContents" in url for url, _ in requests)
    docs = requests[0][1]["requests"]
    assert len(docs) == 2
    assert [d["content"]["parts"][0]["text"] for d in docs] == [
        "title: none | text: first document",
        "title: none | text: second document",
    ]
    assert all(d["outputDimensionality"] == 768 and "taskType" not in d for d in docs)
    assert (
        requests[1][1]["requests"][0]["content"]["parts"][0]["text"]
        == "task: search result | query: find checkout"
    )
    assert not any(backends)
    assert clients and all(c.is_closed for c in clients)


def test_openai_models_can_also_be_selected_from_json(monkeypatch):
    from trace_impact import bootstrap

    real_client = httpx.Client

    class MockClient(real_client):
        def __init__(self, **kwargs):
            super().__init__(
                transport=httpx.MockTransport(lambda _: pytest.fail("Construction must be lazy")), **kwargs
            )

    monkeypatch.setattr(bootstrap.httpx, "Client", MockClient)
    with create_pipeline(
        Settings(openai_api_key="test-only", ingestion_model="ignored-env-model")
    ) as pipeline:
        extractor = pipeline.components.extractors.resolve(
            ExtractionConfig(provider="openai", model="json-selected-model")
        )
        assert extractor.model == "json-selected-model" and extractor.provider == "openai"
        embedding = pipeline.components.embeddings.resolve(
            EmbeddingConfig(provider="openai", model="text-embedding-3-small", dimensions=256)
        )
        assert embedding.profile.dimensions == 256 and embedding.profile.provider == "openai"


def test_request_pacer_waits_between_calls_without_real_sleep():
    now = [100.0]
    delays = []

    def sleep(delay):
        delays.append(delay)
        now[0] += delay

    pacer = RequestPacer(5, clock=lambda: now[0], sleep=sleep)
    pacer.wait()
    now[0] += 2
    pacer.wait()
    assert delays == [10.0]
