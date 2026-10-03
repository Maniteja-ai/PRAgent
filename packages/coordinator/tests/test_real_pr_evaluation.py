import json
from pathlib import Path

import pytest

from trace_coordinator.evaluation.real_pr import (
    RealPrDataset,
    ReviewProvenance,
    evaluate_real_prs,
    real_pr_dataset_schema,
    real_pr_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/real-pr-v2/dataset.json"
PREDICTIONS = ROOT / "evaluation/real-pr-v2/candidate-predictions.json"


def test_real_pr_draft_scores_relevance_and_faithfulness_without_release_claim():
    report = evaluate_real_prs(DATASET, PREDICTIONS)
    assert report["status"] == "DRAFT_EVALUATED"
    assert report["release_eligible"] is False
    assert report["dataset"]["cases"] == 6
    assert report["dataset"]["held_out_cases"] == 2
    assert report["metrics"]["relevance"]["combined"]["precision"] == 1
    assert report["metrics"]["relevance"]["combined"]["recall"] == 1
    assert report["metrics"]["faithfulness"] == 1
    assert report["metrics"]["citation_validity"] == 1
    rendered = real_pr_markdown(report)
    assert "labels visible: **true**" in rendered


def test_real_pr_faithfulness_rejects_an_existing_but_unrelated_citation(tmp_path):
    predictions = json.loads(PREDICTIONS.read_text())
    predictions["cases"][0]["findings"][0]["evidence_ids"] = [
        "github:pr:1199:file:src/checkout/views/saleor-checkout/saleor-checkout.tsx"
    ]
    path = tmp_path / "predictions.json"
    path.write_text(json.dumps(predictions), encoding="utf-8")
    report = evaluate_real_prs(DATASET, path)
    assert report["status"] == "FAILED"
    assert report["metrics"]["citation_validity"] == 1
    assert report["metrics"]["faithfulness"] < 1


def test_real_pr_approval_requires_a_different_dated_reviewer():
    with pytest.raises(ValueError, match="different from the label author"):
        ReviewProvenance(
            status="APPROVED",
            label_author="same person",
            independent_reviewer="same person",
            reviewed_at="2026-10-03",
            notes="invalid self review",
        )


def test_real_pr_schema_matches_runtime():
    assert RealPrDataset.model_validate_json(DATASET.read_text()).id == "saleor-real-pr-v2-draft"
    assert real_pr_dataset_schema()["title"] == "Real PR relevance and faithfulness dataset"


def test_only_approved_blind_system_predictions_are_release_eligible(tmp_path):
    dataset = json.loads(DATASET.read_text())
    dataset["review"].update(
        status="APPROVED", independent_reviewer="Independent Reviewer", reviewed_at="2026-10-03"
    )
    for case in dataset["cases"]:
        case["source_file"] = str((DATASET.parent / case["source_file"]).resolve())
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    predictions = json.loads(PREDICTIONS.read_text())
    predictions["provenance"].update(kind="system_run", labels_visible=False)
    predictions_path = tmp_path / "predictions.json"
    predictions_path.write_text(json.dumps(predictions), encoding="utf-8")

    report = evaluate_real_prs(dataset_path, predictions_path)
    assert report["status"] == "PASSED"
    assert report["release_eligible"] is True


def test_real_pr_evaluator_rejects_mismatched_prediction_set_and_source_hash(tmp_path):
    predictions = json.loads(PREDICTIONS.read_text())
    predictions["dataset_id"] = "wrong"
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(json.dumps(predictions), encoding="utf-8")
    with pytest.raises(ValueError, match="different dataset"):
        evaluate_real_prs(DATASET, prediction_path)

    dataset = json.loads(DATASET.read_text())
    dataset["cases"][0]["source_sha256"] = "0" * 64
    for case in dataset["cases"]:
        case["source_file"] = str((DATASET.parent / case["source_file"]).resolve())
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    with pytest.raises(ValueError, match="Source hash mismatch"):
        evaluate_real_prs(dataset_path, PREDICTIONS)
