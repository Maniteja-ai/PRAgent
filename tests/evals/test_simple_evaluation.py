"""Generic retrieval scoring and integration boundaries; no model/database calls."""

import json
import math
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from trace_impact.evals import EvaluationConfig, Prediction, evaluate
from trace_impact.evals.cli import main


def write_jsonl(path, values):
    path.write_text("".join(json.dumps(v) + "\n" for v in values), encoding="utf-8")


def setup(tmp_path, cases, predictions=None, **options):
    path = tmp_path / "eval.json"
    value = {
        "dataset": "cases.jsonl",
        "output_directory": "reports",
        "task": "set_retrieval",
        "reference": {"complete": True},
        **options,
    }
    write_jsonl(tmp_path / "cases.jsonl", cases)
    if predictions is not None:
        value["predictions"] = "outputs.jsonl"
        write_jsonl(tmp_path / "outputs.jsonl", predictions)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def case(id, expected, approved=False):
    return {
        "id": id,
        "input": {"query": "test query"},
        "reference": {"expected_ids": expected},
        "review": {"approved": approved},
    }


def report(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_field_mapping_scores_exported_outputs_and_preserves_input_files(tmp_path):
    config = setup(
        tmp_path,
        [case("a", ["x", "y"]), case("b", ["z"]), case("c", [])],
        [
            {"request": "a", "data": {"hits": [{"symbol": "x"}, {"symbol": "wrong"}]}},
            {"request": "b", "data": {"hits": [{"symbol": "z"}]}},
            {"request": "c", "data": {"hits": []}},
        ],
        prediction={"case_id": "request", "items": "data.hits", "item_id": "symbol"},
    )
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.json*")}
    path, summary = evaluate(config)
    assert (summary["tp"], summary["fp"], summary["fn"]) == (2, 1, 1)
    assert summary["precision"] == summary["recall"] == summary["f1"] == pytest.approx(2 / 3)
    assert summary["evidence_status"] == "DRAFT_PROVISIONAL" and summary["release_pass"] is False
    assert report(path)["cases"][2]["negative_case_pass"] is True
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob("*.json*")}
    second, _ = evaluate(config)
    assert second != path and path.exists()


def test_ranked_grades_duplicate_penalty_and_alternative_evidence(tmp_path):
    item = {
        "id": "a",
        "input": {},
        "reference": {
            "grades": {"a": 2, "b": 2, "c": 1, "d": 0},
            "units": [{"acceptable_passage_ids": ["a", "b"]}],
            "universe": ["a", "b", "c", "d"],
        },
    }
    config = setup(
        tmp_path,
        [item],
        [{"id": "a", "retrieved_ids": ["c", "a", "a", "b"]}],
        task="ranked_retrieval",
        k=3,
        reference={
            "complete": True,
            "expected_ids": None,
            "relevance_grades": "reference.grades",
            "candidate_universe": "reference.universe",
            "evidence_units": "reference.units",
        },
    )
    path, summary = evaluate(config)
    result = report(path)["cases"][0]
    assert (result["tp"], result["fp"], result["fn"]) == (1, 2, 1)
    assert result["precision_at_k"] == pytest.approx(1 / 3)
    assert result["reciprocal_rank"] == 0.5
    assert result["evidence_recall_at_k"] == 1
    assert result["duplicate_ids"] == ["a"]
    expected_ndcg = (1 + 3 / math.log2(3)) / (3 + 3 / math.log2(3) + 1 / math.log2(4))
    assert result["ndcg_at_k"] == pytest.approx(expected_ndcg)
    assert summary["mean_evidence_recall_at_k"] == 1


def test_missing_rows_are_not_silently_dropped(tmp_path):
    config = setup(tmp_path, [case("a", ["x"]), case("b", ["y"])], [{"id": "a", "retrieved_ids": ["x"]}])
    path, summary = evaluate(config)
    assert summary["completion_rate"] == summary["recall"] == 0.5
    assert summary["quality_gate"] == "INCOMPLETE"
    assert report(path)["cases"][1]["execution_status"] == "MISSING"


def test_unknown_candidate_and_partial_reference_withhold_precision(tmp_path):
    row = case("a", ["x"])
    row["reference"]["universe"] = ["x", "y"]
    config = setup(
        tmp_path,
        [row],
        [{"id": "a", "retrieved_ids": ["x", "outside"]}],
        reference={"complete": True, "candidate_universe": "reference.universe"},
    )
    path, summary = evaluate(config)
    assert summary["quality_gate"] == "INCOMPLETE" and summary["precision"] is None
    assert report(path)["cases"][0]["unjudged_ids"] == ["outside"]
    config = setup(
        tmp_path, [case("a", ["x"])], [{"id": "a", "retrieved_ids": ["x"]}], reference={"complete": False}
    )
    assert evaluate(config)[1]["quality_gate"] == "INCOMPLETE"


def test_empty_reference_ranked_case_does_not_score_raw_search_as_failed_abstention(tmp_path):
    config = setup(
        tmp_path,
        [case("a", [])],
        [{"id": "a", "retrieved_ids": ["nearest-context"]}],
        task="ranked_retrieval",
    )
    path, summary = evaluate(config)
    assert summary["positive_cases"] == 0
    assert summary["mean_precision_at_k"] is None and summary["recall"] is None
    assert report(path)["cases"][0]["precision_at_k"] is None


def test_adapter_receives_inputs_without_labels_and_errors_do_not_leak_messages(tmp_path):
    config = setup(tmp_path, [case("a", ["x"]), case("b", ["y"])])
    received = []

    class Adapter:
        def predict(self, item):
            received.append(item)
            assert item.inputs == {"query": "test query"} and not hasattr(item, "reference")
            if item.id == "b":
                raise RuntimeError("private-api-key-must-not-be-recorded")
            return Prediction(retrieved_ids=["x"])

    path, summary = evaluate(config, adapter=Adapter())
    assert len(received) == 2 and summary["recall"] == 0.5
    assert report(path)["cases"][1]["error_type"] == "RuntimeError"
    assert "private-api-key" not in path.read_text(encoding="utf-8")


def test_release_review_guard_gates_and_application_status(tmp_path):
    config = setup(tmp_path, [case("a", ["x"])], [{"id": "a", "retrieved_ids": ["x"]}], mode="release")
    with pytest.raises(ValueError, match="approved"):
        evaluate(config)
    row = case("a", ["x"], approved=True)
    row["reference"]["status"] = "OK"
    config = setup(
        tmp_path,
        [row],
        [{"id": "a", "retrieved_ids": ["x"], "status": "WRONG"}],
        mode="release",
        reference={"complete": True, "expected_status": "reference.status"},
        gates={"precision_min": 0.9, "recall_min": 0.9},
    )
    assert evaluate(config)[1]["quality_gate"] == "FAIL"
    write_jsonl(tmp_path / "outputs.jsonl", [{"id": "a", "retrieved_ids": ["x"], "status": "OK"}])
    assert evaluate(config)[1]["release_pass"] is True
    write_jsonl(tmp_path / "outputs.jsonl", [{"id": "a", "retrieved_ids": ["wrong"], "status": "OK"}])
    assert evaluate(config)[1]["quality_gate"] == "FAIL"


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_case",
        "unknown_case",
        "bad_ids",
        "duplicate_key",
        "bad_units",
        "missing_field",
        "null_status",
    ],
)
def test_invalid_exports_and_references_fail_before_adapter_calls(tmp_path, problem):
    rows, outputs = [case("a", ["x"])], [{"id": "a", "retrieved_ids": ["x"]}]
    options = {}
    if problem == "duplicate_case":
        outputs *= 2
    elif problem == "unknown_case":
        outputs[0]["id"] = "other"
    elif problem == "bad_ids":
        outputs[0]["retrieved_ids"] = [42]
    elif problem == "missing_field":
        outputs[0] = {"id": "a"}
    elif problem == "null_status":
        rows[0]["reference"]["status"] = None
        options = {"reference": {"complete": True, "expected_status": "reference.status"}}
    elif problem == "bad_units":
        rows[0]["reference"]["units"] = [{"acceptable_passage_ids": ["unknown"]}]
        options = {"reference": {"complete": True, "evidence_units": "reference.units"}}
    config = setup(tmp_path, rows, outputs, **options)
    if problem == "duplicate_key":
        (tmp_path / "outputs.jsonl").write_text('{"id":"a","id":"b","retrieved_ids":[]}\n')
    with pytest.raises(ValueError):
        evaluate(config)
    assert not (tmp_path / "reports").exists()


def test_cli_outputs_report_schema_and_nonzero_failure_codes(tmp_path, monkeypatch, capsys):
    config = setup(
        tmp_path, [case("a", ["x"])], [{"id": "a", "retrieved_ids": []}], gates={"recall_min": 0.8}
    )
    monkeypatch.setattr(sys, "argv", ["trace-eval", "run", str(config)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["quality_gate"] == "FAIL"
    monkeypatch.setattr(sys, "argv", ["trace-eval", "schema", "--output", str(tmp_path / "schema.json")])
    main()
    schema = json.loads((tmp_path / "schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(config.read_text()))
    assert EvaluationConfig.model_validate_json(config.read_text()).output_directory == Path("reports")


def test_current_vector_draft_format_can_be_scored_without_transforming_dataset(tmp_path):
    root = Path(__file__).resolve().parents[2]
    first = json.loads(
        (root / "evaluation/datasets/saleor-retrieval-v0.1/labels/vector-cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    config = setup(
        tmp_path,
        [first],
        [{"id": "V01", "retrieved_ids": ["P23", "P16", "P24"]}],
        task="ranked_retrieval",
        reference={
            "complete": True,
            "expected_ids": None,
            "relevance_grades": "reference.relevance_grades",
            "candidate_universe": "reference.candidate_universe",
            "evidence_units": "reference.evidence_units",
        },
    )
    _, summary = evaluate(config)
    assert summary["mean_evidence_recall_at_k"] == 1
    assert summary["mean_precision_at_k"] == 0.4
    assert summary["release_pass"] is False
