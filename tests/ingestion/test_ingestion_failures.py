"""Ingestion boundary and recovery tests; no network or retrieval calls."""

import json
from pathlib import Path

import httpx
import pytest

from trace_impact import create_pipeline
from trace_impact.ingestion.config import load_project
from trace_impact.ingestion.documents.loaders import LocalFileLoader, WebLoader
from trace_impact.ingestion.models import Extraction, RawDocument
from trace_impact.ingestion.service import require_complete_corpus
from trace_impact.ingestion.storage.neo4j_requirement_store import Neo4jRequirementStore
from trace_impact.pipeline import collect, extract

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("damage", ["source_mismatch", "missing_document_chunks", "empty_text"])
def test_corrupt_corpus_is_rejected_before_processing(tmp_path, damage):
    _, corpus = collect(ROOT / "configs/ingestion/example/project.json", tmp_path)
    if damage == "source_mismatch":
        corpus.chunks[0].source_id = "unrelated-source"
    elif damage == "missing_document_chunks":
        orphan = corpus.snapshots[0].model_copy(update={"id": "orphan-document"})
        corpus.snapshots.append(orphan)
    else:
        corpus.chunks[0].text = "   "
    with pytest.raises(ValueError):
        require_complete_corpus(corpus)
    # The public database adapter must enforce the same rule when called directly.
    with pytest.raises(ValueError):
        object.__new__(Neo4jRequirementStore).load(corpus)


@pytest.mark.parametrize("failure", ["too_large", "redirect_loop", "not_found", "timeout"])
def test_web_loader_fails_boundedly(tmp_path, monkeypatch, failure):
    calls = []
    monkeypatch.setattr("trace_impact.ingestion.documents.loaders.MAX_BYTES", 20)
    project = load_project(ROOT / "configs/ingestion/example/project.json")
    project.allowed_document_hosts = ["docs.example.com"]
    source = project.sources[0].model_copy(update={"location": "https://docs.example.com/spec"})

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic timeout", request=request)
        if failure == "not_found":
            return httpx.Response(404)
        if failure == "redirect_loop":
            return httpx.Response(302, headers={"location": "/spec"})
        return httpx.Response(200, content=b"x" * 21)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises((ValueError, httpx.HTTPError)):
            list(WebLoader(client).load(source, project, tmp_path))
    assert len(calls) == (6 if failure == "redirect_loop" else 1)


def test_local_file_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("trace_impact.ingestion.documents.loaders.MAX_BYTES", 4)
    project = load_project(ROOT / "configs/ingestion/example/project.json")
    (tmp_path / "spec.md").write_bytes(b"12345")
    with pytest.raises(ValueError, match="exceeds"):
        list(LocalFileLoader().load(project.sources[0], project, tmp_path))


@pytest.mark.parametrize("mode", ["empty", "duplicate"])
def test_invalid_loader_output_is_partial_not_success(tmp_path, mode):
    project = load_project(ROOT / "configs/ingestion/example/project.json")
    project.sources[0].loader = "invalid_fixture"
    path = tmp_path / "project.json"
    path.write_text(project.model_dump_json())

    class Loader:
        version = "test-only"

        def load(self, *args):
            if mode == "duplicate":
                for _ in range(2):
                    yield RawDocument(key="same", location="test", content=b"# Rule\n\nA user can search.")

    with create_pipeline() as app:
        app.components.loaders.register("invalid_fixture", Loader())
        folder, corpus = app.collect(path, tmp_path / "runs")
    assert corpus.errors and not corpus.snapshots and not corpus.chunks
    assert json.loads((folder / "inventory.json").read_text())["status"] == "PARTIAL"


def test_changing_extractor_invalidates_cache(tmp_path):
    folder, corpus = collect(ROOT / "configs/ingestion/example/project.json", tmp_path)

    class Extractor:
        provider, model, prompt_version = "test-only", "none", "test"
        fingerprint = "v1"
        calls = 0

        def extract(self, *args):
            self.calls += 1
            return Extraction(requirements=[], no_requirement_reason="Test fixture")

    adapter = Extractor()
    assert extract(folder, adapter).status == "COMPLETE"
    adapter.fingerprint = "v2"
    assert extract(folder, adapter).status == "COMPLETE"
    assert adapter.calls == 2 * len(corpus.chunks)


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_empty_extraction_requires_meaningful_reason(tmp_path, reason):
    folder, _ = collect(ROOT / "configs/ingestion/example/project.json", tmp_path)

    class Extractor:
        provider, model, prompt_version, fingerprint = "test-only", "none", "test", "empty"

        def extract(self, *args):
            return Extraction(requirements=[], no_requirement_reason=reason)

    result = extract(folder, Extractor())
    assert result.status == "PARTIAL" and result.errors and not result.processed_chunk_ids
    assert not list((folder / "extraction-cache").glob("*.json"))
