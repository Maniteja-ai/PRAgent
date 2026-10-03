"""Dataset integrity tests only; not measurements of retrieval quality."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import ValidationError

from trace_impact.evals.validation import dataset as module

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evaluation/datasets/saleor-retrieval-v0.1"


def test_draft_dataset_is_internally_valid_and_not_declared_gold():
    result = module.validate(DATASET)
    assert result["structural_integrity"] == "PASS"
    assert result["semantic_gold_approved"] is False
    assert result["retrieval_executed"] is False


@pytest.mark.parametrize(
    "problem",
    [
        "corrupt_source",
        "missing_qrel",
        "promote_unreviewed",
        "missing_review",
        "unknown_case_field",
        "duplicate_key",
    ],
)
def test_validator_rejects_dataset_corruption(tmp_path, problem):
    root = tmp_path / "dataset"
    shutil.copytree(DATASET, root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = root / "labels/vector-cases.jsonl"
    rows = [json.loads(s) for s in target.read_text(encoding="utf-8").splitlines()]
    if problem == "corrupt_source":
        (root / "review/sources/vouchers.md").write_text("changed", encoding="utf-8")
    elif problem == "promote_unreviewed":
        manifest.update(golden_release=True, status="FROZEN")
    elif problem == "missing_review":
        target = root / "review/queue.jsonl"
        rows = [json.loads(s) for s in target.read_text(encoding="utf-8").splitlines()][1:]
    elif problem == "missing_qrel":
        del rows[0]["reference"]["relevance_grades"]["P01"]
    elif problem == "unknown_case_field":
        rows[0]["undeclared"] = "unexpected"
    if problem not in {"corrupt_source", "promote_unreviewed"}:
        content = "".join(json.dumps(r) + "\n" for r in rows)
        if problem == "duplicate_key":
            content = content.replace('"id": "V01"', '"id": "V01", "id": "V99"', 1)
        target.write_text(content, encoding="utf-8")
        manifest["files"][target.relative_to(root).as_posix()] = hashlib.sha256(
            target.read_bytes()
        ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        module.validate(root)
