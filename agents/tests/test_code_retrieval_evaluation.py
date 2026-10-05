"""Unit tests for retrieval evaluation metrics and dataset validation."""

from evaluation.code_retrieval.run_live import EvaluationDataset, _micro_scores, _scores


def test_scores_calculate_precision_recall_and_f1() -> None:
    actual = _scores(frozenset({"a", "b"}), frozenset({"b", "c"}))

    assert actual == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 1,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
    }


def test_micro_scores_aggregate_cases_before_dividing() -> None:
    actual = _micro_scores(
        (
            {"scores": {"true_positive": 1, "false_positive": 0, "false_negative": 1}},
            {"scores": {"true_positive": 1, "false_positive": 3, "false_negative": 0}},
        )
    )

    assert actual == {
        "true_positive": 2,
        "false_positive": 3,
        "false_negative": 1,
        "precision": 0.4,
        "recall": 0.6667,
        "f1": 0.5,
    }


def test_dataset_rejects_missing_expected_paths() -> None:
    raw = {
        "dataset_id": "test",
        "status": "DRAFT_PENDING_HUMAN_REVIEW",
        "indexed_revision": "abc",
        "label_basis": "direct imports",
        "cases": [
            {
                "id": "case-1",
                "changed_path": "src/changed.ts",
                "title": "Change",
                "query": "Find direct consumers",
                "expected_paths": [],
                "label_evidence": "graph edge",
            }
        ],
    }

    try:
        EvaluationDataset.model_validate(raw)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected dataset validation to reject an empty expected path list")
