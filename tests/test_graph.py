import os
import uuid
from pathlib import Path

import pytest

from trace_impact.extraction import validate_candidate
from trace_impact.graph import Neo4jStore
from trace_impact.models import Candidate, ExtractionRun
from trace_impact.pipeline import collect

ROOT = Path(__file__).resolve().parents[1]


def test_mismatched_extraction_is_rejected_before_database_access(tmp_path):
    _, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    extraction = ExtractionRun(
        id="e",
        corpus_run_id="wrong-run",
        project_id=corpus.project.project_id,
        provider="test",
        model="test",
        prompt_version="test",
        created_at="now",
        status="COMPLETE",
        processed_chunk_ids=[],
        no_requirement_chunks={},
        errors=[],
        requirements=[],
    )
    store = object.__new__(Neo4jStore)
    with pytest.raises(ValueError, match="does not belong"):
        store.load(corpus, extraction)


def test_partial_extraction_is_not_published(tmp_path):
    _, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    extraction = ExtractionRun(
        id="e",
        corpus_run_id=corpus.run_id,
        project_id=corpus.project.project_id,
        provider="test",
        model="test",
        prompt_version="test",
        created_at="now",
        status="PARTIAL",
        processed_chunk_ids=[],
        no_requirement_chunks={},
        errors=[],
        requirements=[],
    )
    with pytest.raises(ValueError, match="partial extraction"):
        object.__new__(Neo4jStore).load(corpus, extraction)

    extraction.status = "COMPLETE"
    with pytest.raises(ValueError, match="every corpus chunk"):
        object.__new__(Neo4jStore).load(corpus, extraction)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_NEO4J_INTEGRATION") != "1", reason="Explicit live Neo4j integration opt-in is required"
)
def test_live_neo4j_roundtrip_is_idempotent(tmp_path):
    # Uses a unique project namespace; leaves auditable test data, never clears a shared database.
    _, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    corpus.project.project_id = "integration-" + uuid.uuid4().hex
    # Recollect with the new ID so snapshot and chunk IDs are isolated too.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "spec.md").write_text((ROOT / "projects/example/spec.md").read_text())
    (config_dir / "project.json").write_text(corpus.project.model_dump_json())
    _, corpus = collect(config_dir / "project.json", tmp_path / "runs")
    chunk = next(c for c in corpus.chunks if c.heading.endswith("Search"))
    req = validate_candidate(
        Candidate(
            statement="A visitor can search by book title",
            actor="visitor",
            behavior="search",
            preconditions=[],
            expected_outcome="Matching titles are returned",
            exceptions=[],
            layer="frontend",
            support="documented",
            evidence_quote="A visitor can search the catalog by book title.",
            uncertainty=[],
        ),
        chunk,
        corpus.snapshots[0],
        corpus.project.project_id,
    )
    extraction = ExtractionRun(
        id=uuid.uuid4().hex,
        corpus_run_id=corpus.run_id,
        project_id=corpus.project.project_id,
        provider="integration-test-fixture",
        model="none",
        prompt_version="test",
        created_at="now",
        status="COMPLETE",
        processed_chunk_ids=[c.id for c in corpus.chunks],
        no_requirement_chunks={},
        errors=[],
        requirements=[req],
    )
    store = Neo4jStore.from_env()
    try:
        store.initialize()
        store.load(corpus, extraction)
        first = store.counts(corpus.project.project_id)
        store.load(corpus, extraction)
        assert store.counts(corpus.project.project_id) == first
        assert first["requirements"] == 1
        assert first["chunks"] == len(corpus.chunks)
    finally:
        store.close()
