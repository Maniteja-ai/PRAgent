"""Deterministic saved-output scoring. No ingestion, model or database dependencies."""

import hashlib
import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from trace_impact.evals.config import EvaluationConfig, Prediction


@dataclass(frozen=True)
class CaseInput:
    id: str
    inputs: dict
    limit: int | None


class PipelineAdapter(Protocol):
    def predict(self, case: CaseInput) -> Prediction: ...


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode(text):
    def bad_number(value):
        raise ValueError("Non-finite numbers are not valid evaluation data")

    return json.loads(text, object_pairs_hook=_object, parse_constant=bad_number)


def _rows(path, content=None):
    content = path.read_bytes() if content is None else content
    rows = [_decode(line) for line in content.decode("utf-8-sig").splitlines() if line.strip()]
    _require(all(isinstance(row, dict) for row in rows), "Each JSONL record must be an object")
    return rows


def _get(data, path, *, default=None, required=True):
    if path is None:
        return default
    current = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            if required:
                raise ValueError(f"Missing configured field: {path}")
            return default
        current = current[part]
    return current


def _id(value):
    _require(isinstance(value, str) and bool(value), "Case and result IDs must be nonempty strings")
    return value


def _ids(value):
    _require(isinstance(value, list), "Expected a JSON list of result IDs")
    return [_id(v) for v in value]


def _unique_rows(rows, field):
    result = {}
    for row in rows:
        id = _id(_get(row, field))
        _require(id not in result, "Duplicate case ID")
        result[id] = row
    return result


def _reference(row, config):
    fields = config.reference
    if fields.relevance_grades:
        grades = _get(row, fields.relevance_grades)
        _require(isinstance(grades, dict), "Relevance grades must be an object")
        for id, grade in grades.items():
            _id(id)
            _require(type(grade) is int and 0 <= grade <= 10, "Grades must be integers from 0 to 10")
        relevant = {id for id, grade in grades.items() if grade >= fields.relevant_grade}
        judged = set(grades)
    else:
        ids = _ids(_get(row, fields.expected_ids))
        _require(len(set(ids)) == len(ids), "Duplicate expected IDs")
        relevant, grades, judged = set(ids), dict.fromkeys(ids, 1), None
    if fields.candidate_universe:
        universe = _ids(_get(row, fields.candidate_universe))
        _require(len(set(universe)) == len(universe), "Duplicate candidate IDs")
        _require(relevant <= set(universe), "Expected IDs are outside the candidate universe")
        if judged is not None:
            _require(
                judged == set(universe), "Relevance grades must cover the full declared candidate universe"
            )
        judged = set(universe)
    units = None
    if fields.evidence_units:
        raw_units = _get(row, fields.evidence_units)
        _require(isinstance(raw_units, list), "Evidence units must be a list")
        units = []
        for unit in raw_units:
            alternatives = set(_ids(_get(unit, fields.unit_alternatives)))
            _require(
                bool(alternatives) and alternatives <= relevant, "Evidence alternatives must be relevant IDs"
            )
            units.append(alternatives)
    expected_status = _get(row, fields.expected_status) if fields.expected_status else None
    if fields.expected_status:
        _id(expected_status)
    approval = _get(row, fields.approval, default=False, required=False)
    _require(type(approval) is bool, "Review approval must be true or false")
    return relevant, grades, judged, units, expected_status, approval


class SavedOutputsAdapter:
    """Translate exported JSONL through field mappings; never invokes the target pipeline."""

    def __init__(self, rows, fields):
        self.predictions = {}
        for id, row in _unique_rows(rows, fields.case_id).items():
            status = _get(row, fields.execution_status, default="OK", required=False)
            values = _get(row, fields.items, default=[], required=status == "OK")
            _require(isinstance(values, list), "Prediction items must be a list")
            ids = [_id(_get(v, fields.item_id)) if fields.item_id else _id(v) for v in values]
            self.predictions[id] = Prediction(
                retrieved_ids=ids,
                execution_status=status,
                result_status=_get(row, fields.result_status, required=False),
            )

    def predict(self, case):
        return self.predictions.get(case.id, Prediction(execution_status="MISSING"))


def _ratio(a, b):
    return a / b if b else None


def _score(id, ref, prediction, config):
    relevant, grades, judged, units, expected_status, approved = ref
    results = (
        prediction.retrieved_ids[: config.k]
        if config.task == "ranked_retrieval"
        else prediction.retrieved_ids
    )
    seen, matches, wrong, unknown, duplicates = set(), [], [], [], []
    for value in results:
        if value in seen:
            duplicates.append(value)
            continue
        seen.add(value)
        if value in relevant:
            matches.append(value)
        elif (judged is not None and value in judged) or (judged is None and config.reference.complete):
            wrong.append(value)
        else:
            unknown.append(value)
    tp, fp, fn = len(matches), len(wrong) + len(duplicates), len(relevant - seen)
    quality_known = not unknown and config.reference.complete
    precision = _ratio(tp, tp + fp) if quality_known else None
    recall = _ratio(tp, tp + fn)
    f1 = _ratio(2 * tp, 2 * tp + fp + fn) if quality_known else None
    result = {
        "id": id,
        "execution_status": prediction.execution_status,
        "reference_approved": approved,
        "status_match": None if expected_status is None else prediction.result_status == expected_status,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "matched_ids": matches,
        "incorrect_ids": wrong,
        "missing_ids": sorted(relevant - seen),
        "unjudged_ids": unknown,
        "duplicate_ids": duplicates,
        "returned": len(results),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "reference_complete": config.reference.complete,
    }
    if config.task == "ranked_retrieval":
        # Empty reference sets are not evidence of search failure: raw nearest-neighbor search
        # can return context for unanswerable questions. Abstention requires a separate contract.
        seen_ranks, dcg = set(), 0.0
        for rank, value in enumerate(results, 1):
            if value not in seen_ranks:
                dcg += (2 ** grades.get(value, 0) - 1) / math.log2(rank + 1)
                seen_ranks.add(value)
        ideal = sum(
            (2**grade - 1) / math.log2(i + 2)
            for i, grade in enumerate(sorted(grades.values(), reverse=True)[: config.k])
        )
        result.update(
            precision_at_k=tp / config.k if relevant and quality_known else None,
            reciprocal_rank=next((1 / i for i, value in enumerate(results, 1) if value in relevant), 0.0)
            if relevant and quality_known
            else None,
            ndcg_at_k=_ratio(dcg, ideal) if relevant and quality_known else None,
            evidence_recall_at_k=_ratio(sum(bool(unit & seen) for unit in units), len(units))
            if units is not None
            else None,
        )
    else:
        result["negative_case_pass"] = (
            not results and prediction.execution_status == "OK" if not relevant else None
        )
    return result


def evaluate(config_path: str | Path, *, adapter: PipelineAdapter | None = None):
    """Score outputs or call a trusted adapter with input-only case objects. Return report path and summary."""
    config_path = Path(config_path).resolve(strict=True)
    raw_config = config_path.read_bytes()
    config = EvaluationConfig.model_validate(_decode(raw_config.decode("utf-8-sig")))

    def resolve(p):
        return (config_path.parent / p).resolve()

    dataset_path = resolve(config.dataset)
    dataset_bytes = dataset_path.read_bytes()
    rows = _unique_rows(_rows(dataset_path, dataset_bytes), config.reference.case_id)
    _require(bool(rows), "Dataset has no cases")
    references = {id: _reference(row, config) for id, row in rows.items()}
    if config.mode == "release":
        _require(config.reference.complete, "Release evaluation requires complete scoped labels")
        _require(
            all(ref[-1] for ref in references.values()),
            "Release evaluation requires approved reference cases",
        )
    prediction_hash = None
    if adapter is None:
        _require(config.predictions is not None, "Provide a predictions file or Python adapter")
        predictions_path = resolve(config.predictions)
        prediction_bytes = predictions_path.read_bytes()
        prediction_hash = hashlib.sha256(prediction_bytes).hexdigest()
        adapter = SavedOutputsAdapter(_rows(predictions_path, prediction_bytes), config.prediction)
        _require(set(adapter.predictions) <= set(rows), "Predictions contain case IDs outside the dataset")
    else:
        _require(config.predictions is None, "Remove predictions from config when supplying a Python adapter")
    # Validate all adapter inputs before invoking user code; reference labels are never passed.
    inputs = {}
    for id, row in rows.items():
        values = _get(row, config.reference.inputs)
        _require(isinstance(values, dict), "Case inputs must be a JSON object")
        inputs[id] = CaseInput(id, values, config.k if config.task == "ranked_retrieval" else None)
    results = []
    for id in rows:
        error_type = None
        try:
            prediction = Prediction.model_validate(adapter.predict(inputs[id]))
        except Exception as exc:
            # Do not write arbitrary exception messages: provider messages may contain secrets.
            prediction, error_type = Prediction(execution_status="ERROR"), type(exc).__name__
        scored = _score(id, references[id], prediction, config)
        scored["error_type"] = error_type
        results.append(scored)
    positive = [r for r in results if r["tp"] + r["fn"]]
    aggregate = positive if config.task == "ranked_retrieval" else results
    tp, fp, fn = (sum(r[key] for r in aggregate) for key in ("tp", "fp", "fn"))
    complete = (
        all(r["execution_status"] == "OK" and not r["unjudged_ids"] for r in results)
        and config.reference.complete
    )
    reviewed = all(ref[-1] for ref in references.values())
    summary = {
        "cases": len(results),
        "completed": sum(r["execution_status"] == "OK" for r in results),
        "completion_rate": sum(r["execution_status"] == "OK" for r in results) / len(results),
        "positive_cases": len(positive),
        "empty_reference_cases": len(results) - len(positive),
        "aggregation_scope": "answerable cases only; failures remain in recall denominator"
        if config.task == "ranked_retrieval"
        else "all scoped cases",
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _ratio(tp, tp + fp) if complete else None,
        "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn) if complete else None,
        "evidence_status": "APPROVED" if reviewed else "DRAFT_PROVISIONAL",
        "unjudged_count": sum(len(r["unjudged_ids"]) for r in results),
        "status_mismatches": sum(r["status_match"] is False for r in results),
    }
    if config.task == "ranked_retrieval":
        for metric in ("precision_at_k", "reciprocal_rank", "ndcg_at_k", "evidence_recall_at_k"):
            values = [r[metric] for r in positive if r[metric] is not None]
            summary["mean_" + metric] = sum(values) / len(values) if values else None
            summary[metric + "_eligible_cases"] = len(values)
    gate = "NOT_CONFIGURED"
    if not complete:
        gate = "INCOMPLETE"
    elif summary["status_mismatches"]:
        gate = "FAIL"
    elif any(v is not None for v in (config.gates.precision_min, config.gates.recall_min)):
        gate = "PASS"
        for metric, threshold in [
            ("precision", config.gates.precision_min),
            ("recall", config.gates.recall_min),
        ]:
            if threshold is not None:
                if summary[metric] is None:
                    gate = "INCOMPLETE"
                    break
                if summary[metric] < threshold:
                    gate = "FAIL"
    summary["quality_gate"] = gate
    summary["release_pass"] = config.mode == "release" and gate == "PASS" and reviewed
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "config": config.model_dump(mode="json"),
        "fingerprints": {
            "config_sha256": hashlib.sha256(raw_config).hexdigest(),
            "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "predictions_sha256": prediction_hash,
        },
        "adapter": type(adapter).__name__,
        "summary": summary,
        "cases": results,
        "limits": [
            "Scores compare IDs and declared judgments; they do not verify code semantics, graph paths or source hashes.",
            "Approval is a recorded dataset assertion, not proof of reviewer identity.",
            "Full dataset integrity and independent annotation review remain separate checks.",
        ],
    }
    folder = resolve(config.output_directory) / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    target = folder / "report.json"
    temp = folder / "report.tmp"
    temp.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    temp.replace(target)
    return target, summary
