"""Stage contracts, failure isolation, real SDK serialization and indexed retrieval."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tests.ingestion.test_providers import mock_google
from trace_impact import Settings, create_pipeline
from trace_impact.retrieval import (
    Passage,
    PassageScore,
    Ranking,
    RetrievalConfig,
    RetrievalScope,
    RetrievalService,
    Selection,
    StageConfig,
    build_retrieval_service,
    load_retrieval_config,
)
from trace_impact.retrieval.config import (
    GeminiRerankerOptions,
    ThresholdOptions,
    TopKOptions,
    retrieval_schema,
)
from trace_impact.retrieval.rerankers.gemini import SCORE_KIND, GeminiReranker, Grade, Grades
from trace_impact.retrieval.rerankers.identity import IdentityReranker
from trace_impact.retrieval.selectors import ScoreThresholdSelector, TopKSelector
from trace_impact.shared.errors import ConfigurationError, ProviderError

ROOT = Path(__file__).resolve().parents[2]
SCOPE = RetrievalScope(project_id="shop", run_id="v1")


def test_cli_json_preserves_unicode_on_legacy_windows_stdout(monkeypatch):
    import io

    from trace_impact.cli import dispatch

    content = "Unicode: \u200b \u914d\u9001"
    result = service(passage(text=content)).run("query", SCOPE)
    raw = io.BytesIO()
    output = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr("sys.stdout", output)
    dispatch(
        SimpleNamespace(command="retrieve", run_dir=Path("unused"), query="query", config=None),
        SimpleNamespace(retrieve=lambda *args: result),
    )
    output.flush()
    restored = json.loads(raw.getvalue().decode("cp1252"))
    assert restored["candidates"][0]["text"] == content


def passage(id="p1", **updates):
    return Passage.model_validate(
        {
            "id": id,
            "scope": SCOPE,
            "source_id": "docs",
            "text": "Shipping vouchers require a shipping address.",
            "artifact_path": "docs/shipping.md",
            "retrieval_score": 0.8,
            **updates,
        }
    )


class FixedRetriever:
    def __init__(self, *passages):
        self.passages = passages

    def retrieve(self, query, scope, limit):
        return self.passages


def service(*passages, reranker=None, selector=None, limit=10):
    return RetrievalService(
        FixedRetriever(*passages),
        reranker or IdentityReranker(),
        selector or TopKSelector(TopKOptions()),
        limit,
    )


@pytest.mark.parametrize("query", ["", "  ", "x" * 4001])
def test_invalid_query_fails_before_stage_execution(query):
    with pytest.raises(ValueError, match="Query"):
        service().run(query, SCOPE)


@pytest.mark.parametrize("kind", ["duplicate", "scope", "overflow"])
def test_retriever_boundary(kind):
    data = {
        "duplicate": (passage(), passage()),
        "scope": (passage(scope=RetrievalScope(project_id="other", run_id="v1")),),
        "overflow": (passage(), passage("p2")),
    }[kind]
    with pytest.raises(ValueError):
        service(*data, limit=1 if kind == "overflow" else 10).run("query", SCOPE)


@pytest.mark.parametrize(
    "scores",
    [
        (),
        (PassageScore(id="invented", score=1),),
        (PassageScore(id="p1", score=1), PassageScore(id="p1", score=2)),
        (PassageScore(id="p1", score=3, quote="invented quotation"),),
    ],
)
def test_reranker_cannot_drop_duplicate_invent_or_misquote(scores):
    ranker = SimpleNamespace(rerank=lambda q, c: Ranking(score_kind="test", scores=scores))
    with pytest.raises(ValueError):
        service(passage(), reranker=ranker).run("query", SCOPE)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_scores_rejected(value):
    with pytest.raises(ValidationError):
        PassageScore(id="p", score=value)
    with pytest.raises(ValidationError):
        passage(retrieval_score=value)


@pytest.mark.parametrize("ids", [("unknown",), ("p1", "p1"), ("p2", "p1")])
def test_selector_cannot_invent_duplicate_or_reorder(ids):
    selector = SimpleNamespace(select=lambda q, c, r: Selection(ids=ids, reason="test"))
    with pytest.raises(ValueError):
        service(passage(), passage("p2", retrieval_score=0.5), selector=selector).run("query", SCOPE)


def test_stable_order_and_metadata_survive_untrusted_plugin_mutation():
    def rerank(query, candidates):
        candidates[0].metadata["nested"]["owner"] = "changed"
        return Ranking(
            score_kind="test", scores=(PassageScore(id="p2", score=3), PassageScore(id="p1", score=3))
        )

    original = passage(metadata={"nested": {"owner": "original"}})
    result = service(original, passage("p2"), reranker=SimpleNamespace(rerank=rerank)).run("query", SCOPE)
    assert result.selection.ids == ("p1", "p2")
    assert result.candidates[0].metadata == original.metadata == {"nested": {"owner": "original"}}
    assert result.coverage == "NOT_EVALUATED"


def test_threshold_deduplication_cap_and_no_forced_fallback():
    selector = ScoreThresholdSelector(ThresholdOptions(score_kind="test", min_score=3, max_results=1))
    candidates = (passage(), passage("p2"), passage("p3", text="Different support"))
    ranking = Ranking(score_kind="test", scores=tuple(PassageScore(id=p.id, score=3) for p in candidates))
    decision = selector.select("q", candidates, ranking)
    assert decision.ids == ("p1",) and decision.limit_reached
    selector = ScoreThresholdSelector(ThresholdOptions(score_kind="retrieval-score-v1", min_score=0.9))
    result = service(passage(), selector=selector).run("query", SCOPE)
    assert result.status == "NO_EVIDENCE" and result.selection.ids == ()
    with pytest.raises(ValueError, match="score scale"):
        selector.select("q", candidates, ranking)


def test_empty_candidates_do_not_call_gemini():
    ranker = GeminiReranker(
        SimpleNamespace(invoke=lambda *a, **k: pytest.fail("Unexpected model call")),
        GeminiRerankerOptions(model="test"),
    )
    result = service(reranker=ranker).run("query", SCOPE)
    assert result.status == "NO_EVIDENCE"


def response(items=None, **metadata):
    return {
        "parsed": Grades(
            items=items if items is not None else (Grade(id="p1", grade=3, quote=passage().text),)
        ),
        "raw": SimpleNamespace(
            response_metadata=metadata, additional_kwargs={}, usage_metadata={"input_tokens": 8}
        ),
        "parsing_error": None,
    }


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {"parsing_error": "invalid"},
        response(finish_reason="MAX_TOKENS"),
        response(prompt_feedback={"block_reason": "SAFETY"}),
        response(status="failed"),
        response((Grade(id="p1", grade=3, quote=""),)),
        response((Grade(id="p1", grade=3, quote="not present"),)),
        response(()),
    ],
)
def test_invalid_model_outputs_fail_explicitly(bad):
    ranker = GeminiReranker(SimpleNamespace(invoke=lambda *a, **k: bad), GeminiRerankerOptions(model="test"))
    with pytest.raises(ValueError):
        ranker.rerank("q", (passage(),))


def test_timeout_is_a_failure_not_empty_success_and_close_is_idempotent():
    closed = []

    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("private request detail")

    ranker = GeminiReranker(
        SimpleNamespace(invoke=fail),
        GeminiRerankerOptions(model="test"),
        close=lambda: closed.append(True),
        request_errors=(httpx.HTTPError,),
    )
    with pytest.raises(ProviderError) as caught:
        ranker.rerank("q", (passage(),))
    assert "private" not in str(caught.value)
    ranker.close()
    ranker.close()
    assert closed == [True]


def test_input_budget_rejects_without_silent_truncation():
    ranker = GeminiReranker(
        SimpleNamespace(invoke=lambda *a, **k: pytest.fail("Unexpected call")),
        GeminiRerankerOptions(model="test", max_input_chars=1000),
    )
    with pytest.raises(ValueError, match="budget"):
        ranker.rerank("q", (passage(text="x" * 1001),))


def test_registry_extension_and_config_validation_before_provider_creation():
    with create_pipeline() as app:
        config = RetrievalConfig(reranker=StageConfig(provider="custom"))
        app.components.rerankers.register_configured_factory("CUSTOM", lambda c: IdentityReranker())
        result = build_retrieval_service(FixedRetriever(passage()), config, app.components).run("q", SCOPE)
        assert result.selection.ids == ("p1",)
        invalid = RetrievalConfig(
            reranker=StageConfig(provider="gemini", options={"model": "test"}),
            selector=StageConfig(provider="top_k", options={"typo": 1}),
        )
        with pytest.raises(ValidationError):
            build_retrieval_service(FixedRetriever(), invalid, app.components)
        with pytest.raises(ConfigurationError, match="Unknown"):
            build_retrieval_service(
                FixedRetriever(), RetrievalConfig(reranker=StageConfig(provider="typo")), app.components
            )


def test_saved_schema_and_example_configs_match_runtime():
    with create_pipeline() as app:
        schema = retrieval_schema(app.components)
        assert schema == json.loads((ROOT / "schemas/retrieval/retrieval.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        paths = [p for p in (ROOT / "configs/retrieval").glob("*.json") if p.name != "saleor-impact.json"]
        assert len(paths) >= 6, "Retrieval configuration examples were not discovered"
        for path in paths:
            Draft202012Validator(schema).validate(json.loads(path.read_text()))
            config = load_retrieval_config(path)
            for registry, stage in (
                (app.components.rerankers, config.reranker),
                (app.components.selectors, config.selector),
            ):
                registry.validate_options(stage.provider, stage.options)
    assert StageConfig(provider=" GeMiNi ").provider == "gemini"


def test_real_langchain_sdk_serializes_prompt_and_closes_client(monkeypatch):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
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
                                        {"items": [{"id": "p1", "grade": 3, "quote": passage().text}]}
                                    )
                                }
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30},
            },
        )

    clients, _ = mock_google(monkeypatch, handler)
    with create_pipeline(Settings(gemini_api_key="test-only-key")) as app:
        config = RetrievalConfig(
            reranker=StageConfig(
                provider="gemini", options={"model": "gemini-3.5-flash-lite", "max_retries": 0}
            ),
            selector=StageConfig(
                provider="score_threshold", options={"score_kind": SCORE_KIND, "min_score": 3}
            ),
        )
        result = build_retrieval_service(FixedRetriever(passage()), config, app.components).run(
            "Shipping requirements?", SCOPE
        )
    assert result.selection.ids == ("p1",) and result.ranking.usage["total_tokens"] == 30
    assert len(calls) == 1 and calls[0]["generationConfig"]["responseMimeType"] == "application/json"
    assert all(c.is_closed for c in clients)


def test_index_to_reranker_to_selection_and_cli_with_real_local_qdrant(tmp_path, monkeypatch, capsys):
    from tests.integration.test_ingestion_e2e import QUOTE, setup_project
    from trace_impact.cli import dispatch
    from trace_impact.retrieval.documents.adapters import IndexedCorpusRetriever

    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(str(request.url))
        if "batchEmbedContents" in str(request.url):
            return httpx.Response(
                200, json={"embeddings": [{"values": [1.0] + [0.0] * 767} for _ in body["requests"]]}
            )
        payload = json.loads(body["contents"][0]["parts"][0]["text"])
        output = {"items": [{"id": p["id"], "grade": 3, "quote": QUOTE} for p in payload["passages"]]}
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": json.dumps(output)}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    clients, _ = mock_google(monkeypatch, handler)
    project = setup_project(tmp_path, live=True)
    settings = Settings(gemini_api_key="test-only-key", qdrant_path=str(tmp_path / "vectors"))
    with create_pipeline(settings) as app:
        run, corpus = app.collect(project, tmp_path / "runs")
        app.index(run)
        config = ROOT / "configs/retrieval/gemini.json"
        result = app.retrieve(run, "Can a visitor search by book title?", config)
        assert result.status == "EVIDENCE_FOUND"
        assert result.selection.ids == tuple(c.id for c in corpus.chunks)
        assert all(p.scope.run_id == corpus.run_id for p in result.candidates)
        assert all(s.quote == QUOTE for s in result.ranking.scores)
        before = len(calls)
        default = app.retrieve(run, "Search books with no reranking?")
        assert default.ranking.score_kind == "retrieval-score-v1"
        assert len(default.selection.ids) <= 5
        assert calls[before:] and all("batchEmbedContents" in url for url in calls[before:])
        dispatch(
            SimpleNamespace(
                command="retrieve",
                run_dir=run,
                query="Search books?",
                config=None,
            ),
            app,
        )
        assert json.loads(capsys.readouterr().out)["selection"]["ids"] == list(result.selection.ids)
        dispatch(SimpleNamespace(command="retrieval-schema", output=tmp_path / "schema.json"), app)
        assert (tmp_path / "schema.json").exists()
        adapter = IndexedCorpusRetriever(app, run, corpus)
        with pytest.raises(ValueError, match="scope"):
            adapter.retrieve("q", SCOPE, 5)
        # Incomplete publication fails before embedding/reranking, rather than returning partial evidence.
        index = json.loads((run / "vector-index.json").read_text())
        index["status"] = "PARTIAL"
        (run / "vector-index.json").write_text(json.dumps(index))
        before = len(calls)
        with pytest.raises(ValueError, match="complete index"):
            app.retrieve(run, "Search books?", config)
        assert len(calls) == before
    assert all(c.is_closed for c in clients)


def test_provider_length_compatibility_keeps_strict_local_validation():
    from trace_impact.retrieval.rerankers.gemini import provider_schema

    wire = json.dumps(provider_schema())
    assert "maxItems" not in wire and "maxLength" not in wire
    bad = {"parsed": {"items": [{"id": "p1", "grade": 3, "quote": "x" * 601}]}}
    ranker = GeminiReranker(SimpleNamespace(invoke=lambda *a, **k: bad), GeminiRerankerOptions(model="test"))
    with pytest.raises(ValueError, match="invalid structured"):
        ranker.rerank("q", (passage(),))


def test_editor_schema_supports_nested_plugin_options_and_normalized_names():
    from trace_impact.shared.component_config import ComponentDefinition, EmptyOptions

    class Nested(EmptyOptions):
        count: int

    class PluginOptions(EmptyOptions):
        nested: Nested

    with create_pipeline() as app:
        app.components.rerankers.register_configured_factory(
            "custom-ranker",
            lambda c: IdentityReranker(),
            definition=ComponentDefinition("Custom", "Nested options example", PluginOptions),
        )
        validator = Draft202012Validator(retrieval_schema(app.components))
        validator.validate({"reranker": {"provider": " CuStOm-RaNkEr ", "options": {"nested": {"count": 2}}}})
        assert list(
            validator.iter_errors(
                {"reranker": {"provider": "custom-ranker", "options": {"nested": {"count": "wrong"}}}}
            )
        )
        assert list(validator.iter_errors({"reranker": {"provider": " GEMINI "}}))
        assert list(
            validator.iter_errors({"selector": {"provider": "SCORE_THRESHOLD", "options": {"typo": 3}}})
        )
