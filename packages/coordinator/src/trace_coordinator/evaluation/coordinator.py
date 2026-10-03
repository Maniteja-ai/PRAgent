"""Deterministic golden evaluation for coordinator behavior and evidence contracts."""

import hashlib
import json
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter

from trace_coordinator.application.coordinator import Coordinator
from trace_coordinator.config import CallLimits, HumanReviewPolicy
from trace_coordinator.domain.contracts import AnalysisReportPayload, JsonObject, as_json_object
from trace_coordinator.domain.models import AnalysisRequest, Record
from trace_coordinator.infrastructure.adapters.fixtures import FixtureModel, FixtureTool


class GoldenFinding(Record):
    title: str = Field(min_length=1, max_length=300)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)


class GoldenExpected(Record):
    status: Literal["COMPLETED", "COMPLETED_WITH_GAPS", "STOPPED", "FAILED"]
    stop_reason: str
    verification: str = "NOT_RUN"
    review_status: Literal["NOT_REQUESTED", "NOT_ANSWERED", "ANSWERED"] = "NOT_REQUESTED"
    review_question: str | None = None
    findings: tuple[GoldenFinding, ...] = ()
    max_total_calls: int = Field(default=30, ge=1, le=1000)


class GoldenCase(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    request: AnalysisRequest
    tools: dict[str, JsonObject]
    decisions: tuple[JsonObject, ...] = Field(min_length=1, max_length=20)
    human_review: HumanReviewPolicy = Field(default_factory=HumanReviewPolicy)
    expected: GoldenExpected


class DatasetReview(Record):
    status: Literal["APPROVED"]
    reviewed_by: str = Field(min_length=1, max_length=100)
    reviewed_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    basis: str = Field(min_length=1, max_length=1000)


class GoldenDataset(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100)
    review: DatasetReview
    limits: CallLimits = Field(default_factory=lambda: CallLimits(retry_delay_seconds=0))
    minimum_precision: float = Field(default=1, ge=0, le=1)
    minimum_recall: float = Field(default=1, ge=0, le=1)
    minimum_case_pass_rate: float = Field(default=1, ge=0, le=1)
    cases: tuple[GoldenCase, ...] = Field(min_length=1, max_length=100)


_REPORT_ADAPTER = TypeAdapter(AnalysisReportPayload)


def load_dataset(path: str | Path) -> GoldenDataset:
    return GoldenDataset.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))


def evaluate_dataset(path: str | Path, output_directory: str | Path) -> JsonObject:
    source = Path(path).resolve()
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    dataset = GoldenDataset.model_validate_json(raw)
    true_positive = false_positive = false_negative = 0
    case_results: list[JsonObject] = []
    cases_passed = 0
    for case in dataset.cases:
        case_root = output / "state" / case.id
        tools = [FixtureTool(name, value) for name, value in case.tools.items()]
        report = _REPORT_ADAPTER.validate_python(
            Coordinator(
                case_root,
                dataset.limits,
                tools,
                FixtureModel(case.decisions),
                human_review=case.human_review,
            ).run(case.request, "evaluation"),
        )
        report_bytes = (json.dumps(report, indent=2) + "\n").encode()
        report_path = output / "cases" / case.id / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(report_bytes)
        predicted = {item["title"]: set(item["evidence_ids"]) for item in report["findings"]}
        expected = {item.title: set(item.evidence_ids) for item in case.expected.findings}
        matched = {
            title for title, refs in expected.items() if title in predicted and refs <= predicted[title]
        }
        true_positive += len(matched)
        false_positive += len(predicted) - len(matched)
        false_negative += len(expected) - len(matched)
        citations_valid = all(
            ref in report["evidence"] for finding in report["findings"] for ref in finding["evidence_ids"]
        )
        total_calls = sum(item["attempts"] for item in report["tool_usage"])
        passed = (
            report["status"] == case.expected.status
            and report["stop_reason"] == case.expected.stop_reason
            and report["verification"] == case.expected.verification
            and report["human_review"]["status"] == case.expected.review_status
            and (
                case.expected.review_question is None
                or any(
                    item["question"] == case.expected.review_question
                    for item in report["human_review"]["requests"]
                )
            )
            and set(predicted) == set(expected)
            and matched == set(expected)
            and citations_valid
            and total_calls <= case.expected.max_total_calls
            and all(item["attempts"] <= 5 for item in report["tool_usage"])
        )
        cases_passed += int(passed)
        case_results.append(
            as_json_object(
                {
                    "id": case.id,
                    "passed": passed,
                    "expected_findings": sorted(expected),
                    "predicted_findings": sorted(predicted),
                    "citations_valid": citations_valid,
                    "review_status": report["human_review"]["status"],
                    "total_calls": total_calls,
                    "report": {
                        "path": str(report_path),
                        "sha256": hashlib.sha256(report_bytes).hexdigest(),
                        "bytes": len(report_bytes),
                    },
                }
            )
        )
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    pass_rate = cases_passed / len(case_results)
    passed = (
        precision >= dataset.minimum_precision
        and recall >= dataset.minimum_recall
        and pass_rate >= dataset.minimum_case_pass_rate
    )
    return as_json_object(
        {
            "schema_version": 1,
            "status": "PASSED" if passed else "FAILED",
            "dataset": {
                "id": dataset.id,
                "path": str(source),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "review": dataset.review.model_dump(mode="json"),
            },
            "metrics": {
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
                "precision": precision,
                "recall": recall,
                "case_pass_rate": pass_rate,
                "cases_passed": cases_passed,
                "cases_total": len(case_results),
            },
            "thresholds": {
                "precision": dataset.minimum_precision,
                "recall": dataset.minimum_recall,
                "case_pass_rate": dataset.minimum_case_pass_rate,
            },
            "cases": case_results,
        }
    )


def _stable_evaluation_view(report: JsonObject) -> JsonObject:
    """Return only behaviorally meaningful fields, excluding output paths and hashes."""
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Evaluation report cases are invalid")
    return as_json_object(
        {
            "status": report["status"],
            "metrics": report["metrics"],
            "cases": [
                {
                    "id": item["id"],
                    "passed": item["passed"],
                    "expected_findings": item["expected_findings"],
                    "predicted_findings": item["predicted_findings"],
                    "citations_valid": item["citations_valid"],
                    "review_status": item["review_status"],
                    "total_calls": item["total_calls"],
                }
                for value in cases
                for item in (as_json_object(value),)
            ],
        }
    )


def evaluate_stability(path: str | Path, output_directory: str | Path, repetitions: int = 100) -> JsonObject:
    """Repeat the full offline coordinator contract and measure output agreement."""
    if not 1 <= repetitions <= 100:
        raise ValueError("repetitions must be between 1 and 100")
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    attempts: list[JsonObject] = []
    signatures: list[str] = []
    started = time.perf_counter()
    for number in range(1, repetitions + 1):
        attempt_started = time.perf_counter()
        report = evaluate_dataset(path, output / "attempts" / f"attempt-{number:03d}")
        stable = _stable_evaluation_view(report)
        signature = hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        signatures.append(signature)
        attempts.append(
            as_json_object(
                {
                    "attempt": number,
                    "status": report["status"],
                    "signature": signature,
                    "seconds": round(time.perf_counter() - attempt_started, 6),
                }
            )
        )
    elapsed = time.perf_counter() - started
    passed_count = sum(item.get("status") == "PASSED" for item in attempts)
    distinct = len(set(signatures))
    seconds = [item.get("seconds") for item in attempts]
    numeric_seconds = [float(value) for value in seconds if isinstance(value, (int, float))]
    return as_json_object(
        {
            "schema_version": 1,
            "status": "PASSED" if passed_count == repetitions and distinct == 1 else "FAILED",
            "dataset": {
                "path": str(Path(path).resolve()),
                "sha256": hashlib.sha256(Path(path).resolve().read_bytes()).hexdigest(),
            },
            "metrics": {
                "scheduled": repetitions,
                "completed": len(attempts),
                "passed": passed_count,
                "pass_rate": passed_count / repetitions,
                "distinct_behavioral_outputs": distinct,
                "mean_seconds": round(elapsed / repetitions, 6),
                "max_seconds": max(numeric_seconds),
            },
            "attempts": attempts,
            "limits": [
                "Uses deterministic fixture tools and model decisions; it does not measure live LLM variance.",
                "Repeats the same reviewed contract cases; it does not represent independent PR scenarios.",
            ],
        }
    )


def stability_markdown(report: JsonObject) -> str:
    metrics = as_json_object(report["metrics"])
    raw_limits = report["limits"]
    if not isinstance(raw_limits, list) or not all(isinstance(item, str) for item in raw_limits):
        raise ValueError("Stability report limits must be a list of strings")
    return "\n".join(
        [
            "# Coordinator stability evaluation",
            "",
            f"Status: {report['status']}",
            "",
            "| Metric | Result |",
            "| --- | ---: |",
            f"| Scheduled runs | {metrics['scheduled']} |",
            f"| Completed runs | {metrics['completed']} |",
            f"| Passed runs | {metrics['passed']} |",
            f"| Pass rate | {metrics['pass_rate']:.3f} |",
            f"| Distinct behavioral outputs | {metrics['distinct_behavioral_outputs']} |",
            f"| Mean duration | {metrics['mean_seconds']:.3f} s |",
            f"| Maximum duration | {metrics['max_seconds']:.3f} s |",
            "",
            "## Limits",
            "",
            *[f"- {item}" for item in raw_limits],
            "",
        ]
    )


def evaluation_markdown(report: JsonObject) -> str:
    metrics = as_json_object(report["metrics"])
    dataset = as_json_object(report["dataset"])
    thresholds = as_json_object(report["thresholds"])
    raw_cases = report["cases"]
    if not isinstance(raw_cases, list):
        raise ValueError("Evaluation report cases must be a list")
    cases = [as_json_object(item) for item in raw_cases]
    lines = [
        "# Coordinator golden evaluation",
        "",
        f"Status: {report['status']}",
        "",
        f"Dataset: {dataset['id']} ({dataset['sha256']})",
        "",
        "| Metric | Result | Threshold |",
        "| --- | ---: | ---: |",
        f"| Precision | {metrics['precision']:.3f} | {thresholds['precision']:.3f} |",
        f"| Recall | {metrics['recall']:.3f} | {thresholds['recall']:.3f} |",
        f"| Case pass rate | {metrics['case_pass_rate']:.3f} | {thresholds['case_pass_rate']:.3f} |",
        "",
        "| Case | Result | Calls |",
        "| --- | --- | ---: |",
    ]
    lines.extend(
        f"| {item['id']} | {'PASS' if item['passed'] else 'FAIL'} | {item['total_calls']} |" for item in cases
    )
    return "\n".join(lines) + "\n"


def dataset_schema() -> JsonObject:
    return as_json_object(
        {
            **GoldenDataset.model_json_schema(),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Coordinator golden dataset",
        }
    )
