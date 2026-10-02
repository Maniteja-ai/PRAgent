"""Behavioral contracts for recovery and dependency boundaries; no live model calls."""

import ast
import json
import logging
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from trace_impact.application.services import ExtractionService
from trace_impact.domain.errors import ArtifactError, ExtractionError, RunBusyError
from trace_impact.domain.models import Extraction, ExtractionRun, load_project
from trace_impact.domain.policies import GroundingPolicy
from trace_impact.infrastructure.artifacts import FileArtifactRepository
from trace_impact.infrastructure.events import JsonEventSink
from trace_impact.infrastructure.langchain_extractor import LangChainRequirementExtractor
from trace_impact.infrastructure.sources import HttpSourceReader
from trace_impact.pipeline import collect
from trace_impact.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_inner_layers_cannot_import_adapters_or_provider_libraries():
    for layer in ["domain", "application"]:
        for path in (ROOT / "src/trace_impact" / layer).glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                for module in modules:
                    assert not any(
                        part in module.split(".")
                        for part in [
                            "infrastructure",
                            "bootstrap",
                            "langchain_openai",
                            "openai",
                            "neo4j",
                            "httpx",
                        ]
                    ), path
                    if layer == "domain":
                        assert "application" not in module.split("."), path


def test_artifact_write_failure_preserves_previous_checkpoint(tmp_path, monkeypatch):
    repo = FileArtifactRepository()
    path = tmp_path / "checkpoint.json"
    repo.write(path, {"old": True})

    def disk_failure(*args):
        raise OSError("Test injected failure")

    monkeypatch.setattr("trace_impact.infrastructure.artifacts.os.replace", disk_failure)
    with pytest.raises(OSError):
        repo.write(path, {"new": True})
    assert json.loads(path.read_text()) == {"old": True}
    assert list(tmp_path.iterdir()) == [path]


def test_run_lock_rejects_overlapping_writer_and_releases_after_failure(tmp_path):
    repo = FileArtifactRepository()
    with pytest.raises(RuntimeError):
        with repo.lock(tmp_path):
            with pytest.raises(RunBusyError):
                with FileArtifactRepository().lock(tmp_path):
                    pytest.fail("Overlapping writer entered")
            raise RuntimeError("Test simulated interruption")
    with repo.lock(tmp_path):
        pass


def test_redirect_cannot_escape_document_allowlist(tmp_path):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://unapproved.example/private"})

    project = load_project(ROOT / "projects/example/project.json")
    project.allowed_document_hosts = ["docs.example.com"]
    source = project.sources[0].model_copy(update={"location": "https://docs.example.com/start"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="outside"):
            HttpSourceReader(client).read(source, project, tmp_path)
    assert requests == ["https://docs.example.com/start"]


def test_langchain_rejects_refusal_and_truncated_outputs(tmp_path):
    _, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    for response in [
        {"raw": AIMessage(content="", additional_kwargs={"refusal": "declined"}), "parsed": None},
        {
            "raw": AIMessage(content="", response_metadata={"finish_reason": "length"}),
            "parsed": Extraction(requirements=[], no_requirement_reason="test"),
        },
    ]:
        adapter = LangChainRequirementExtractor(
            RunnableLambda(lambda _, result=response: result),
            provider="test",
            model="test",
            configuration_id="test",
        )
        with pytest.raises(ExtractionError):
            adapter.extract(corpus.chunks[0], corpus.snapshots[0], corpus.project.scope)


class RecoverableExtractor:
    provider, model, fingerprint, prompt_version = "test", "test", "recoverable-v1", "test"

    def __init__(self):
        self.fail = True
        self.calls = 0

    def extract(self, chunk, snapshot, scope):
        self.calls += 1
        if self.fail:
            self.fail = False
            raise ExtractionError("Test failure")
        return Extraction(requirements=[], no_requirement_reason="Labeled test double")


def service(adapter):
    return ExtractionService(
        adapter, FileArtifactRepository(), GroundingPolicy(), JsonEventSink(logging.getLogger("test.events"))
    )


def test_failed_chunk_is_retried_without_recalling_successful_chunks(tmp_path):
    folder, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    adapter = RecoverableExtractor()
    first = service(adapter).extract(folder)
    assert first.status == "PARTIAL"
    second = service(adapter).extract(folder)
    assert second.status == "COMPLETE"
    assert adapter.calls == len(corpus.chunks) + 1
    assert len(list((folder / "extractions").glob("*.json"))) == 2


def test_interrupted_collection_is_rejected_even_without_recorded_errors(tmp_path):
    folder, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    corpus.snapshots = []
    FileArtifactRepository().write(folder / "corpus.json", corpus)
    with pytest.raises(ValueError, match="partial"):
        service(RecoverableExtractor()).extract(folder)


def test_corrupt_cache_does_not_trigger_hidden_billable_retries(tmp_path):
    folder, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    adapter = RecoverableExtractor()
    adapter.fail = False
    service(adapter).extract(folder)
    first = next((folder / "extraction-cache").glob("*.json"))
    first.write_text("broken")
    with pytest.raises(ArtifactError):
        service(adapter).extract(folder)
    assert adapter.calls == len(corpus.chunks)
    assert FileArtifactRepository().read(folder / "extraction.json", ExtractionRun).status == "PARTIAL"


def test_authority_changes_produce_new_snapshot_identity(tmp_path):
    config = load_project(ROOT / "projects/example/project.json")
    (tmp_path / "spec.md").write_text((ROOT / "projects/example/spec.md").read_text())
    path = tmp_path / "project.json"
    path.write_text(config.model_dump_json())
    _, first = collect(path, tmp_path / "runs")
    config.sources[0].authority = "backend_contract"
    path.write_text(config.model_dump_json())
    _, second = collect(path, tmp_path / "runs")
    assert first.snapshots[0].id != second.snapshots[0].id


def test_secrets_are_redacted_in_settings_repr():
    settings = Settings(openai_api_key="unit-test-secret", neo4j_password="unit-test-password")
    assert "unit-test-secret" not in repr(settings)
    assert "unit-test-password" not in settings.model_dump_json()


def test_langchain_openai_responses_contract_with_mock_http(tmp_path):
    """Exercise the real LangChain and OpenAI SDK wire path against a labeled fake response."""
    from langchain_openai import ChatOpenAI

    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_unit_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "test-model",
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "type": "message",
                        "id": "msg_unit_test",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "requirements": [],
                                        "no_requirement_reason": "Mock response for SDK contract test",
                                    }
                                ),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    _, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        llm = ChatOpenAI(
            model="test-model",
            api_key="unit-test-key",
            http_client=client,
            use_responses_api=True,
            store=False,
            max_retries=0,
        )
        adapter = LangChainRequirementExtractor(
            llm.with_structured_output(Extraction, method="json_schema", strict=True, include_raw=True),
            provider="openai",
            model="test-model",
            configuration_id="test",
        )
        result = adapter.extract(corpus.chunks[0], corpus.snapshots[0], corpus.project.scope)
    assert result.requirements == []
    assert captured[0]["store"] is False
    assert captured[0]["text"]["format"]["type"] == "json_schema"
    assert captured[0]["text"]["format"]["strict"] is True
