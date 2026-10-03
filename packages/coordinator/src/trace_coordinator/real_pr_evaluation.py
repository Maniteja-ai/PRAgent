"""Reviewed real-PR relevance and faithfulness evaluation."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from trace_coordinator.models import Record


class ReviewProvenance(Record):
    status: Literal["DRAFT", "APPROVED"] = "DRAFT"
    label_author: str = Field(min_length=1, max_length=100)
    independent_reviewer: str | None = Field(default=None, max_length=100)
    reviewed_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def independent_approval(self):
        if self.status == "APPROVED" and (
            not self.independent_reviewer
            or self.independent_reviewer.casefold() == self.label_author.casefold()
            or not self.reviewed_at
        ):
            raise ValueError("Approval requires a dated reviewer different from the label author")
        return self


class ExpectedClaim(Record):
    statement: str = Field(min_length=1, max_length=500)
    allowed_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class ExpectedImpact(Record):
    ui_elements: tuple[str, ...] = Field(default=(), max_length=50)
    flows: tuple[str, ...] = Field(default=(), max_length=50)
    requirements: tuple[str, ...] = Field(default=(), max_length=50)
    claims: dict[str, ExpectedClaim] = Field(default_factory=dict)


class RealPrCase(Record):
    id: str = Field(pattern=r"^pr-[1-9][0-9]*$")
    split: Literal["development", "held_out"]
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    pull_request: int = Field(gt=0)
    base_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    head_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    source_file: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected: ExpectedImpact


class RealPrDataset(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100)
    review: ReviewProvenance
    minimum_relevance_precision: float = Field(default=0.8, ge=0, le=1)
    minimum_relevance_recall: float = Field(default=0.8, ge=0, le=1)
    minimum_faithfulness: float = Field(default=0.95, ge=0, le=1)
    minimum_claim_recall: float = Field(default=0.8, ge=0, le=1)
    cases: tuple[RealPrCase, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_cases(self):
        ids = [case.id for case in self.cases]
        prs = [(case.repository.casefold(), case.pull_request) for case in self.cases]
        if len(ids) != len(set(ids)) or len(prs) != len(set(prs)):
            raise ValueError("Real-PR cases must have unique IDs and repository/PR pairs")
        return self


class PredictedFinding(Record):
    claim_id: str = Field(min_length=1, max_length=100)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)


class PredictedImpact(Record):
    id: str = Field(pattern=r"^pr-[1-9][0-9]*$")
    ui_elements: tuple[str, ...] = Field(default=(), max_length=100)
    flows: tuple[str, ...] = Field(default=(), max_length=100)
    requirements: tuple[str, ...] = Field(default=(), max_length=100)
    findings: tuple[PredictedFinding, ...] = Field(default=(), max_length=100)


class PredictionProvenance(Record):
    kind: Literal["candidate_manual", "system_run"]
    generator: str = Field(min_length=1, max_length=200)
    generated_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    labels_visible: bool = Field(
        description="True when the producer could inspect expected labels; such scores cannot gate release."
    )


class PredictionSet(Record):
    schema_version: Literal[1] = 1
    dataset_id: str
    provenance: PredictionProvenance
    cases: tuple[PredictedImpact, ...] = Field(min_length=1, max_length=100)


def _metric(expected, predicted):
    expected, predicted = set(expected), set(predicted)
    tp, fp, fn = len(expected & predicted), len(predicted - expected), len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}


def _merge_metrics(rows):
    tp = sum(row["tp"] for row in rows)
    fp = sum(row["fp"] for row in rows)
    fn = sum(row["fn"] for row in rows)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / (tp + fn) if tp + fn else 1.0,
    }


def evaluate_real_prs(dataset_path, predictions_path):
    dataset_file = Path(dataset_path).resolve()
    predictions_file = Path(predictions_path).resolve()
    dataset = RealPrDataset.model_validate_json(dataset_file.read_text(encoding="utf-8-sig"))
    predictions = PredictionSet.model_validate_json(predictions_file.read_text(encoding="utf-8-sig"))
    if predictions.dataset_id != dataset.id:
        raise ValueError("Predictions target a different dataset")
    by_id = {item.id: item for item in predictions.cases}
    if set(by_id) != {case.id for case in dataset.cases}:
        raise ValueError("Predictions must contain every dataset case exactly once")

    dimensions = {name: [] for name in ("ui_elements", "flows", "requirements")}
    case_results = []
    supported_claims = predicted_claims = expected_claims_found = expected_claims = 0
    valid_citations = total_citations = 0
    for case in dataset.cases:
        source = (dataset_file.parent / case.source_file).resolve()
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != case.source_sha256:
            raise ValueError(f"Source hash mismatch for {case.id}")
        source_value = json.loads(raw)
        evidence_ids = {item["id"] for item in source_value["evidence"]}
        predicted = by_id[case.id]
        scores = {}
        for dimension in dimensions:
            score = _metric(getattr(case.expected, dimension), getattr(predicted, dimension))
            dimensions[dimension].append(score)
            scores[dimension] = score
        found = set()
        finding_results = []
        for finding in predicted.findings:
            predicted_claims += 1
            total_citations += len(finding.evidence_ids)
            valid = set(finding.evidence_ids) <= evidence_ids
            valid_citations += len(finding.evidence_ids) if valid else 0
            reference = case.expected.claims.get(finding.claim_id)
            faithful = bool(
                valid and reference and set(finding.evidence_ids) & set(reference.allowed_evidence_ids)
            )
            if faithful:
                supported_claims += 1
                found.add(finding.claim_id)
            finding_results.append(
                {
                    "claim_id": finding.claim_id,
                    "citations_valid": valid,
                    "faithful": faithful,
                }
            )
        expected_claims += len(case.expected.claims)
        expected_claims_found += len(found)
        case_results.append(
            {
                "id": case.id,
                "split": case.split,
                "relevance": scores,
                "findings": finding_results,
            }
        )

    relevance = {name: _merge_metrics(rows) for name, rows in dimensions.items()}
    combined = _merge_metrics([score for rows in dimensions.values() for score in rows])
    faithfulness = supported_claims / predicted_claims if predicted_claims else 1.0
    claim_recall = expected_claims_found / expected_claims if expected_claims else 1.0
    citation_validity = valid_citations / total_citations if total_citations else 1.0
    thresholds_pass = (
        combined["precision"] >= dataset.minimum_relevance_precision
        and combined["recall"] >= dataset.minimum_relevance_recall
        and faithfulness >= dataset.minimum_faithfulness
        and claim_recall >= dataset.minimum_claim_recall
        and citation_validity == 1
    )
    approved = dataset.review.status == "APPROVED"
    blind_system_run = (
        predictions.provenance.kind == "system_run" and not predictions.provenance.labels_visible
    )
    return {
        "schema_version": 1,
        "status": "PASSED"
        if thresholds_pass and approved and blind_system_run
        else "DRAFT_EVALUATED"
        if thresholds_pass
        else "FAILED",
        "release_eligible": bool(thresholds_pass and approved and blind_system_run),
        "prediction_provenance": predictions.provenance.model_dump(mode="json"),
        "dataset": {
            "id": dataset.id,
            "cases": len(dataset.cases),
            "development_cases": sum(case.split == "development" for case in dataset.cases),
            "held_out_cases": sum(case.split == "held_out" for case in dataset.cases),
            "review": dataset.review.model_dump(mode="json"),
            "path": str(dataset_file),
            "sha256": hashlib.sha256(dataset_file.read_bytes()).hexdigest(),
        },
        "metrics": {
            "relevance": {**relevance, "combined": combined},
            "faithfulness": faithfulness,
            "claim_recall": claim_recall,
            "citation_validity": citation_validity,
            "predicted_claims": predicted_claims,
            "supported_claims": supported_claims,
        },
        "thresholds": {
            "relevance_precision": dataset.minimum_relevance_precision,
            "relevance_recall": dataset.minimum_relevance_recall,
            "faithfulness": dataset.minimum_faithfulness,
            "claim_recall": dataset.minimum_claim_recall,
            "citation_validity": 1.0,
        },
        "cases": case_results,
        "limits": [
            "DRAFT_EVALUATED is development feedback, not independently approved ground truth.",
            "Predictions produced with labels visible are scorer smoke tests, not model accuracy.",
            "Faithfulness is reference-based evidence support; it does not use an LLM-as-judge.",
        ],
    }


def real_pr_markdown(report):
    metrics = report["metrics"]
    lines = [
        "# Real-PR relevance and faithfulness evaluation",
        "",
        f"Status: **{report['status']}**",
        "",
        f"Release eligible: **{str(report['release_eligible']).lower()}**",
        "",
        f"Dataset: `{report['dataset']['id']}` with {report['dataset']['cases']} cases "
        f"({report['dataset']['held_out_cases']} held out).",
        "",
        f"Prediction source: `{report['prediction_provenance']['kind']}`; labels visible: "
        f"**{str(report['prediction_provenance']['labels_visible']).lower()}**.",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        f"| Combined relevance precision | {metrics['relevance']['combined']['precision']:.3f} |",
        f"| Combined relevance recall | {metrics['relevance']['combined']['recall']:.3f} |",
        f"| Faithfulness | {metrics['faithfulness']:.3f} |",
        f"| Claim recall | {metrics['claim_recall']:.3f} |",
        f"| Citation validity | {metrics['citation_validity']:.3f} |",
        "",
        "## Review status",
        "",
        f"Label author: {report['dataset']['review']['label_author']}",
        "",
        f"Independent reviewer: {report['dataset']['review']['independent_reviewer'] or 'PENDING'}",
        "",
    ]
    lines.extend(f"- {item}" for item in report["limits"])
    return "\n".join(lines) + "\n"


def real_pr_dataset_schema():
    return {
        **RealPrDataset.model_json_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Real PR relevance and faithfulness dataset",
    }
