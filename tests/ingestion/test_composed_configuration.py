"""Composed ingestion configuration and non-invasive stage recording."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trace_impact import create_pipeline
from trace_impact.ingestion.configuration import load_ingestion_configuration
from trace_impact.ingestion.configuration.legacy_adapter import (
    to_code_graph_config,
    to_document_project,
)
from trace_impact.ingestion.evaluation import JsonlStageRecorder, record_stage
from trace_impact.ingestion.mappings import JsonMappingCandidateStore, MappingBatch
from trace_impact.ingestion.mappings.models import (
    EntityReference,
    MappingCandidate,
    MappingEvidence,
)
from trace_impact.ingestion.storage.neo4j_code_store import Neo4jCodeStore
from trace_impact.shared.graph_models import GraphEdge, GraphNode, GraphSnapshot

ROOT = Path(__file__).resolve().parents[2]
SALEOR = ROOT / "configs/ingestion/saleor/ingestion.json"


def test_composed_saleor_configuration_resolves_owned_paths_and_legacy_boundaries():
    config = load_ingestion_configuration(SALEOR)

    assert config.entry.project.id == "saleor-storefront"
    assert Path(config.storage.artifacts.run_directory).is_absolute()
    assert Path(config.evaluation.recording.directory).is_absolute()
    assert config.inputs.code is not None
    assert Path(config.inputs.code.repository_path).is_absolute()

    project = to_document_project(config)
    code = to_code_graph_config(config)
    assert project.project_id == config.entry.project.id
    assert project.sources and project.extractor == config.processing.requirements.extractor
    assert code is not None and code.project_id == project.project_id


def test_each_file_rejects_unknown_fields(tmp_path):
    original = json.loads((SALEOR.parent / "runtime.json").read_text(encoding="utf-8"))
    original["surprise"] = True
    runtime = tmp_path / "runtime.json"
    runtime.write_text(json.dumps(original), encoding="utf-8")

    entry = json.loads(SALEOR.read_text(encoding="utf-8"))
    for name in ("inputs", "processing", "storage", "evaluation"):
        source = SALEOR.parent / entry["config"][name]
        (tmp_path / source.name).write_bytes(source.read_bytes())
    (tmp_path / "ingestion.json").write_text(json.dumps(entry), encoding="utf-8")

    with pytest.raises(ValidationError, match="surprise"):
        load_ingestion_configuration(tmp_path / "ingestion.json")


def test_stage_decorator_records_success_and_failure_without_changing_behavior(tmp_path):
    class Example:
        run_id = "run-1"

        def __init__(self):
            self.stage_recorder = JsonlStageRecorder(tmp_path)

        @record_stage("working")
        def working(self, value: int) -> int:
            return value + 1

        @record_stage("broken")
        def broken(self) -> None:
            raise RuntimeError("expected")

    example = Example()
    assert example.working(4) == 5
    with pytest.raises(RuntimeError, match="expected"):
        example.broken()

    observations = [
        json.loads(line)
        for line in (tmp_path / "stage-observations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [(item["stage"], item["status"]) for item in observations] == [
        ("working", "COMPLETED"),
        ("broken", "FAILED"),
    ]
    assert observations[0]["result_sha256"]
    assert observations[1]["error_type"] == "RuntimeError"


def test_candidate_mappings_are_separate_and_neo4j_rejects_them(tmp_path):
    candidate = MappingCandidate(
        id="candidate-1",
        source=EntityReference(kind="CodeSymbol", id="symbol-1"),
        target=EntityReference(kind="UIElement", id="button-1"),
        relationship="RENDERS",
        confidence=0.82,
        evidence=(
            MappingEvidence(
                source="code",
                reference="src/button.tsx:10",
                sha256="a" * 64,
                detail="Component returns the button element",
            ),
        ),
    )
    batch = MappingBatch(run_id="run-1", project_id="project-1", candidates=(candidate,))
    store = JsonMappingCandidateStore(tmp_path)
    path = store.write(batch)
    assert path.exists() and store.read("project-1", "run-1") == batch

    snapshot = GraphSnapshot(
        id="graph-1",
        origin="test",
        nodes=(
            GraphNode(
                id="symbol-1",
                kind="CodeSymbol",
                name="Button",
                project_id="project-1",
                revision="a" * 40,
            ),
            GraphNode(
                id="button-1",
                kind="UIElement",
                name="Apply",
                project_id="project-1",
                revision="a" * 40,
            ),
        ),
        edges=(
            GraphEdge(
                id="edge-1",
                source="symbol-1",
                target="button-1",
                type="RENDERS",
                status="CANDIDATE",
            ),
        ),
    )
    graph = Neo4jCodeStore(object(), "neo4j", "graph-1")
    with pytest.raises(ValueError, match="confirmed relationships only"):
        graph.publish(snapshot)


def test_composed_pipeline_runs_offline_and_writes_auditable_gaps(tmp_path):
    (tmp_path / "spec.md").write_text(
        "# Search\n\nA visitor can search the catalog by title.", encoding="utf-8"
    )
    files = {
        "ingestion.json": {
            "schema_version": 2,
            "project": {"id": "offline-example", "name": "Offline example"},
            "config": {},
        },
        "inputs.json": {
            "schema_version": 1,
            "repository": {
                "url": "https://example.com/repository",
                "baseline_commit": "0" * 40,
                "code_roots": ["src"],
            },
            "baseline_url": "https://example.com",
            "scope": ["search"],
            "documents": {
                "sources": [
                    {
                        "id": "spec",
                        "location": "spec.md",
                        "loader": "local_file",
                        "parser": "markdown",
                        "authority": "frontend_spec",
                        "version": "v1",
                        "scope": ["search"],
                    }
                ]
            },
        },
        "processing.json": {
            "schema_version": 1,
            "requirements": {"enabled": False, "extractor": "langchain", "max_chunks": 10},
            "embeddings": {"enabled": False, "provider": "openai", "batch_size": 4},
        },
        "storage.json": {
            "schema_version": 1,
            "artifacts": {"provider": "local", "run_directory": "runs"},
            "graph": {
                "provider": "neo4j",
                "publish_documents": False,
                "publish_requirements": False,
                "publish_code": False,
                "publish_confirmed_mappings": False,
            },
            "vector": {"provider": "qdrant", "publish_chunks": False},
            "candidates": {"directory": "candidates"},
        },
        "runtime.json": {"schema_version": 1},
        "evaluation.json": {
            "schema_version": 1,
            "recording": {
                "enabled": True,
                "directory": "observations",
                "include_payloads": False,
            },
        },
    }
    for name, value in files.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")

    with create_pipeline() as pipeline:
        manifest = pipeline.run_ingestion(tmp_path / "ingestion.json")

    assert manifest.status == "COMPLETED_WITH_GAPS"
    assert manifest.corpus_run_id
    run_directory = next((tmp_path / "runs/offline-example").iterdir())
    assert (run_directory / "ingestion-manifest.json").exists()
    assert (tmp_path / "candidates/offline-example" / f"{manifest.run_id}.json").exists()
    observations = (tmp_path / "observations/stage-observations.jsonl").read_text(encoding="utf-8")
    assert "collect_documents" in observations
