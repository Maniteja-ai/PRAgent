import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import ChangeSet, FileChange, ToolContext
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.adapters.fixtures import QueryInput
from trace_coordinator.infrastructure.adapters.knowledge import KnowledgeTool


@pytest.fixture
def knowledge(tmp_path):
    pytest.importorskip("trace_impact")
    from trace_impact.shared.graph_models import GraphNode, GraphSnapshot

    graph = GraphSnapshot(
        id="g",
        origin="test",
        edges=(),
        nodes=(
            GraphNode(
                id="s",
                kind="CodeSymbol",
                name="checkout",
                project_id="p",
                revision="a" * 40,
                properties={"path": "checkout.ts"},
            ),
        ),
    )
    (tmp_path / "graph.json").write_text(graph.model_dump_json(), encoding="utf-8")
    for name, data in {
        "corpus.json": {"project": {"project_id": "p"}},
        "vector-index.json": {},
        "retrieval.json": {},
    }.items():
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    app = ApplicationConfig(
        project_id="p",
        repository="owner/repo",
        repository_path=str(tmp_path),
        graph_snapshot_file=str(tmp_path / "graph.json"),
        ingestion_run_directory=str(tmp_path),
        retrieval_config_file=str(tmp_path / "retrieval.json"),
        vector_directory=str(tmp_path / "vectors"),
        baseline={"url": "https://a.example", "revision": "a" * 40},
        patched={"url": "https://b.example", "revision": "b" * 40},
    )
    changes = ChangeSet(
        repository="owner/repo",
        pull_request=1,
        upstream_base="a" * 40,
        upstream_head="b" * 40,
        comparison_base="a" * 40,
        analysis_base="a" * 40,
        analysis_head="b" * 40,
        files=(FileChange(path="checkout.ts", status="M"),),
        patch_sha256="c" * 64,
        historical_replay=True,
        deployment_patch_equivalent=True,
    )
    context = ToolContext(run_id="run", project_id="p", agent_id="coordinator", changes=changes)
    return app, context


def test_graph_adapter_passes_pinned_scope_and_reports_missing_mappings(knowledge, tmp_path):
    from trace_impact.shared.graph_models import ImpactResult

    app, context = knowledge
    seen = []

    def retrieve(graph, query):
        seen.append((graph, query))
        return ImpactResult(status="UNMAPPED", symbol_ids=("s",), code_files=("checkout.ts",))

    tool = KnowledgeTool("knowledge.graph", app, tmp_path / "artifacts")
    result = tool.graph(SimpleNamespace(retrieve_impact=retrieve), context)
    assert seen[0][1].scope.revision == "a" * 40
    assert seen[0][1].changed_files == ("checkout.ts",)
    assert any("UNMAPPED" in g for g in result.gaps)
    assert any("Head code graph" in g for g in result.gaps)


def test_graph_revision_mismatch_fails_before_read(knowledge, tmp_path):
    app, context = knowledge
    changed = context.model_copy(
        update={"changes": context.changes.model_copy(update={"analysis_base": "c" * 40})}
    )
    with pytest.raises(ToolFailure, match="baseline deployment"):
        KnowledgeTool("knowledge.graph", app, tmp_path).graph(None, changed)


def test_mapped_entities_keep_names_revisions_and_behavior_limits(knowledge, tmp_path):
    from trace_impact.shared.graph_models import GraphNode, ImpactResult

    app, context = knowledge
    node = GraphNode(
        id="ui",
        kind="UIElement",
        name="Discount code",
        project_id="p",
        revision="a" * 40,
        properties={"runtime_attribution_verified": False, "behavior_verification": "NOT_RUN"},
    )
    pipeline = SimpleNamespace(
        retrieve_impact=lambda *_: ImpactResult(status="OK", ui_ids=("ui",), evidence_nodes=(node,))
    )
    result = KnowledgeTool("knowledge.graph", app, tmp_path).graph(pipeline, context)
    summary = json.loads(result.evidence[0].summary)
    assert summary[0]["mapped_entities"][0]["name"] == "Discount code"
    assert summary[0]["mapped_entities"][0]["properties"]["behavior_verification"] == "NOT_RUN"
    assert result.evidence[0].metadata["graph_sources"] == [{"graph_id": "g", "revision": "a" * 40}]
    assert any("runtime attribution" in gap for gap in result.gaps)
    assert any("no confirmed requirement" in gap for gap in result.gaps)


def test_document_translation_uses_selected_passages_only(knowledge, tmp_path):
    from trace_impact.retrieval.models import Passage, Ranking, RetrievalResult, Selection

    app, context = knowledge
    passages = tuple(
        Passage(
            id=str(i),
            scope={"project_id": "p", "run_id": "ingestion"},
            source_id="source",
            text="Voucher requirement",
            artifact_path="source.md",
            retrieval_score=0.5,
        )
        for i in range(2)
    )
    result = RetrievalResult(
        query="voucher",
        scope={"project_id": "p", "run_id": "ingestion"},
        candidates=passages,
        ranking=Ranking(score_kind="test", scores=()),
        selection=Selection(ids=("1",), reason="test"),
        status="EVIDENCE_FOUND",
        timings_ms={},
    )
    seen = []

    def retrieve(run, query, config):
        seen.append((run, query, config))
        return result

    tool = KnowledgeTool("knowledge.documents", app, tmp_path / "artifacts")
    translated = tool.documents(SimpleNamespace(retrieve=retrieve), QueryInput(query="voucher"), context)
    assert len(translated.evidence) == 1
    assert translated.evidence[0].metadata["chunk_id"] == "1"
    assert seen[0][2] == Path(app.retrieval_config_file)


def test_corpus_project_mismatch_fails_before_retrieval(knowledge, tmp_path):
    app, context = knowledge
    tool = KnowledgeTool("knowledge.documents", app, tmp_path)
    (tmp_path / "corpus.json").write_text('{"project":{"project_id":"other"}}', encoding="utf-8")
    with pytest.raises(ToolFailure, match="another project"):
        tool.documents(None, QueryInput(query="voucher"), context)


def test_head_graph_covers_added_files_and_pins_both_revisions(knowledge, tmp_path):
    from trace_impact.shared.graph_models import ImpactResult

    app, context = knowledge
    head = json.loads(Path(app.graph_snapshot_file).read_text())
    head["id"] = "head"
    head["nodes"][0]["revision"] = "b" * 40
    path = tmp_path / "head.json"
    path.write_text(json.dumps(head))
    app = app.model_copy(update={"head_graph_snapshot_file": str(path)})
    context = context.model_copy(
        update={
            "changes": context.changes.model_copy(
                update={
                    "files": (*context.changes.files, FileChange(path="new.ts", status="A")),
                }
            )
        }
    )
    seen = []

    def retrieve(graph, query):
        seen.append((graph, query))
        return ImpactResult(status="OK")

    result = KnowledgeTool("knowledge.graph", app, tmp_path).graph(
        SimpleNamespace(retrieve_impact=retrieve), context
    )
    assert [(g, q.scope.revision) for g, q in seen] == [("g", "a" * 40), ("head", "b" * 40)]
    assert seen[0][1].changed_files == ("checkout.ts",)
    assert seen[1][1].changed_files == ("checkout.ts", "new.ts")
    assert not result.gaps
    head["nodes"][0]["revision"] = "c" * 40
    path.write_text(json.dumps(head))
    with pytest.raises(ToolFailure, match="Head graph"):
        KnowledgeTool("knowledge.graph", app, tmp_path).graph(
            SimpleNamespace(retrieve_impact=retrieve), context
        )


def test_added_only_without_head_is_explicit_gap(knowledge, tmp_path):
    app, context = knowledge
    context = context.model_copy(
        update={
            "changes": context.changes.model_copy(
                update={
                    "files": (FileChange(path="new.ts", status="A"),),
                }
            )
        }
    )
    result = KnowledgeTool("knowledge.graph", app, tmp_path).graph(None, context)
    assert any("Added files" in g for g in result.gaps)
    assert json.loads(result.evidence[0].summary) == []


@pytest.mark.parametrize("name", ["knowledge.graph", "knowledge.documents"])
def test_execute_uses_configured_vector_path_disables_retries_and_closes(
    knowledge, tmp_path, monkeypatch, name
):
    import trace_impact

    from trace_coordinator.domain.models import ToolResult

    app, context = knowledge
    seen = []

    class Settings:
        @classmethod
        def from_env(cls):
            return cls()

        def model_copy(self, *, update):
            seen.append(update)
            return self

    @contextmanager
    def pipeline(settings):
        seen.append("open")
        try:
            yield object()
        finally:
            seen.append("closed")

    monkeypatch.setattr(trace_impact, "Settings", Settings)
    monkeypatch.setattr(trace_impact, "create_pipeline", pipeline)
    tool = KnowledgeTool(name, app, tmp_path)
    monkeypatch.setattr(tool, "graph", lambda *args: ToolResult())
    monkeypatch.setattr(tool, "documents", lambda *args: ToolResult())
    tool.execute(QueryInput(query="voucher"), context)
    assert seen == [
        {"qdrant_path": app.vector_directory, "model_retries": 0, "request_timeout": 45},
        "open",
        "closed",
    ]
    with pytest.raises(ToolFailure, match="validated change set"):
        tool.execute(QueryInput(query="voucher"), context.model_copy(update={"changes": None}))
