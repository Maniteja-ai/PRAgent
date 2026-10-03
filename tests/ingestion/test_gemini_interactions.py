"""Interactions wire contract, failure handling and resumable pipeline integration."""

import json

import httpx
import pytest

from tests.ingestion.test_providers import mock_google, project_file
from trace_impact import Settings, create_pipeline
from trace_impact.ingestion.models import ExtractionRun
from trace_impact.shared.errors import ProviderError


def interaction_response(text=None, status="completed"):
    if text is None:
        text = json.dumps({"requirements": [], "no_requirement_reason": "Synthetic test only"})
    return httpx.Response(
        200,
        json={
            "id": "synthetic-interaction",
            "status": status,
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
            "usage": {"total_input_tokens": 10, "total_output_tokens": 10, "total_tokens": 20},
        },
    )


def interactions_project(tmp_path):
    path = project_file(tmp_path)
    data = json.loads(path.read_text())
    data["extractor"].update(provider="gemini_interactions", model="gemini-3.8-flash")
    path.write_text(json.dumps(data))
    return path


def test_interactions_stateless_schema_cache_and_cleanup(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return interaction_response()

    clients, backends = mock_google(monkeypatch, handler)
    with create_pipeline(Settings(gemini_api_key="test-only", model_retries=0)) as app:
        folder, corpus = app.collect(interactions_project(tmp_path), tmp_path / "runs")
        assert app.extract(folder).status == "COMPLETE"
        assert app.extract(folder).status == "COMPLETE"
    assert len(requests) == len(corpus.chunks)
    assert all(r.url.path == "/v1beta/interactions" for r in requests)
    body = json.loads(requests[0].content)
    assert body["store"] is False and "previous_interaction_id" not in body
    assert body["response_format"]["mime_type"] == "application/json"
    assert "requirements" in body["response_format"]["schema"]["properties"]
    assert body["generation_config"] == {"thinking_level": "low", "max_output_tokens": 6000}
    assert json.loads(body["input"])["documentation"] == corpus.chunks[0].text
    assert body["system_instruction"]
    assert not any(backends) and all(c.is_closed for c in clients)


@pytest.mark.parametrize(
    "status,text",
    [
        ("incomplete", None),
        ("failed", None),
        ("requires_action", None),
        ("budget_exceeded", None),
        ("completed", "not json"),
        ("completed", ""),
        ("completed", '{"requirements": []}'),
    ],
)
def test_interactions_invalid_results_are_not_cached(tmp_path, monkeypatch, status, text):
    mock_google(monkeypatch, lambda _: interaction_response(text, status))
    with create_pipeline(Settings(gemini_api_key="test-only", model_retries=0)) as app:
        folder, _ = app.collect(interactions_project(tmp_path), tmp_path / "runs")
        result = app.extract(folder)
    assert result.status == "PARTIAL" and result.errors
    assert not list((folder / "extraction-cache").glob("*.json"))


def test_interactions_outage_stops_then_resumes_completed_cache(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(503, json={"error": {"code": 503, "message": "Synthetic outage"}})
        return interaction_response()

    mock_google(monkeypatch, handler)
    with create_pipeline(Settings(gemini_api_key="test-only", model_retries=0)) as app:
        folder, corpus = app.collect(interactions_project(tmp_path), tmp_path / "runs")
        with pytest.raises(ProviderError):
            app.extract(folder)
        partial = ExtractionRun.model_validate_json((folder / "extraction.json").read_text())
        assert partial.processed_chunk_ids == [corpus.chunks[0].id]
        assert len(calls) == 2
        assert app.extract(folder).status == "COMPLETE"
        assert len(calls) == len(corpus.chunks) + 1


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_interactions_retry_budget(tmp_path, monkeypatch, retries):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"code": 503, "message": "Synthetic outage"}})

    mock_google(monkeypatch, handler)
    with create_pipeline(Settings(gemini_api_key="test-only", model_retries=retries)) as app:
        folder, _ = app.collect(interactions_project(tmp_path), tmp_path / "runs")
        with pytest.raises(ProviderError):
            app.extract(folder)
    assert len(calls) == retries + 1
