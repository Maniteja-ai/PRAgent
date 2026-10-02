import json
from pathlib import Path

import pytest

from trace_impact.documents import make_chunks, normalize, read_source
from trace_impact.extraction import deduplicate, validate_candidate
from trace_impact.models import Candidate, Extraction, Project, Source, load_project
from trace_impact.pipeline import collect, extract

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def example(tmp_path):
    folder, corpus = collect(ROOT / "projects/example/project.json", tmp_path)
    return folder, corpus


def candidate(quote, **changes):
    fields = dict(
        statement="Visitors can search by title",
        actor="visitor",
        behavior="search",
        preconditions=[],
        expected_outcome="Matching books are shown",
        exceptions=[],
        layer="frontend",
        support="documented",
        evidence_quote=quote,
        uncertainty=[],
    )
    fields.update(changes)
    return Candidate(**fields)


def test_html_normalization_keeps_content_not_navigation():
    text = normalize(
        b"<nav>Outside</nav><main><h1>Rules</h1><nav>Noise</nav>"
        b"<p>A visitor can search by title.</p><pre>call_api()</pre></main>",
        "html",
    )
    assert "Noise" not in text and "Outside" not in text
    assert "# Rules" in text and "call_api()" in text
    with pytest.raises(ValueError, match="No main"):
        normalize(b"<body>Navigation only</body>", "html")


def test_headings_in_code_do_not_create_sections_and_oversize_not_lost():
    text = "# Rules\n\nA condition.\n\n```python\n# Not a heading\n" + "x" * 1100 + "\n```\n\n## Next\n\nEnd."
    chunks = make_chunks(text, "snap", "source", 1000)
    assert any(c.oversized for c in chunks)
    assert not any(c.heading == "Not a heading" for c in chunks)
    assert "x" * 1100 in "\n".join(c.text for c in chunks)
    assert chunks[-1].heading == "Rules > Next"


def test_different_application_works_with_no_saleor_logic(example):
    _, corpus = example
    assert not corpus.errors
    assert corpus.project.project_id == "example-library"
    assert any("loan" in chunk.text for chunk in corpus.chunks)
    assert not any("voucher" in chunk.text for chunk in corpus.chunks)


def test_projects_and_versions_have_separate_identity(tmp_path):
    src = ROOT / "projects/example"
    for name in ["one", "two"]:
        dest = tmp_path / name
        dest.mkdir()
        config = load_project(src / "project.json").model_dump()
        config["project_id"] = name
        (dest / "project.json").write_text(json.dumps(config))
        (dest / "spec.md").write_text((src / "spec.md").read_text())
    _, first = collect(tmp_path / "one/project.json", tmp_path / "runs")
    _, second = collect(tmp_path / "two/project.json", tmp_path / "runs")
    assert first.snapshots[0].id != second.snapshots[0].id
    _, repeat = collect(tmp_path / "one/project.json", tmp_path / "runs")
    assert first.snapshots[0].id == repeat.snapshots[0].id
    assert first.run_id != repeat.run_id


def test_bad_citation_rejected_and_backend_to_ui_needs_review(example):
    _, corpus = example
    chunk = next(c for c in corpus.chunks if c.heading.endswith("Search"))
    snap = corpus.snapshots[0]
    invalid = validate_candidate(candidate("A completely invented claim"), chunk, snap, "example")
    assert invalid.validation == "REJECTED"
    quote = "A visitor can search the catalog by book title."
    valid = validate_candidate(candidate(quote), chunk, snap, "example")
    assert valid.validation == "GROUNDED_CANDIDATE"
    snap.authority = "backend_contract"
    uncertain = validate_candidate(candidate(quote), chunk, snap, "example")
    assert uncertain.validation == "NEEDS_REVIEW"


def test_dedup_does_not_merge_different_preconditions(example):
    _, corpus = example
    chunk = next(c for c in corpus.chunks if c.heading.endswith("Search"))
    snap = corpus.snapshots[0]
    first = validate_candidate(candidate("A visitor can search the catalog by book title."), chunk, snap, "p")
    second = validate_candidate(
        candidate("A visitor can search the catalog by book title.", preconditions=["Only if signed in"]),
        chunk,
        snap,
        "p",
    )
    assert len(deduplicate([first, first, second])) == 2


def test_path_escape_and_unapproved_host_are_rejected(tmp_path):
    project = load_project(ROOT / "projects/example/project.json")
    source = project.sources[0].model_copy(update={"location": "../private.md"})
    with pytest.raises(ValueError, match="inside"):
        read_source(source, project, tmp_path)
    source.location = "https://unapproved.example/doc"
    with pytest.raises(ValueError, match="outside"):
        read_source(source, project, tmp_path)


def test_duplicate_source_ids_rejected():
    data = load_project(ROOT / "projects/example/project.json").model_dump()
    data["sources"] *= 2
    with pytest.raises(ValueError, match="unique"):
        Project.model_validate(data)


class EmptyTestExtractor:
    """Test double only, never registered as a production extraction provider."""

    provider = "unit-test"
    model = "test-model"
    fingerprint = "unit-test-v1"
    prompt_version = "test-v1"

    def __init__(self):
        self.calls = 0

    def extract(self, chunk, snapshot, scope):
        self.calls += 1
        return Extraction(requirements=[], no_requirement_reason="Test double does not extract facts")


def test_resume_uses_cache_and_budget_is_explicitly_partial(example):
    folder, corpus = example
    adapter = EmptyTestExtractor()
    first = extract(folder, adapter, max_chunks=1)
    assert first.status == "PARTIAL"
    second = extract(folder, adapter, max_chunks=100)
    assert second.status == "COMPLETE"
    assert adapter.calls == len(corpus.chunks)
    assert set(second.processed_chunk_ids) == {c.id for c in corpus.chunks}


def test_partial_collection_cannot_start_extraction(tmp_path):
    config = load_project(ROOT / "projects/example/project.json")
    config.sources = [
        Source(
            id="missing",
            location="missing.md",
            format="markdown",
            authority="frontend_spec",
            version="v1",
            scope=["search"],
        )
    ]
    path = tmp_path / "project.json"
    path.write_text(config.model_dump_json())
    folder, corpus = collect(path, tmp_path / "runs")
    assert corpus.errors
    with pytest.raises(ValueError, match="partial"):
        extract(folder, EmptyTestExtractor())
