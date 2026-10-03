"""One reproducible, fail-closed evaluation campaign across the complete system."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from trace_coordinator.models import Record


class CampaignThresholds(Record):
    vector_recall_at_five: float = Field(default=0.9, ge=0, le=1)
    vector_required_evidence_recall: float = Field(default=1.0, ge=0, le=1)
    vector_mrr: float = Field(default=0.85, ge=0, le=1)
    reranker_precision: float = Field(default=0.95, ge=0, le=1)
    reranker_recall: float = Field(default=0.95, ge=0, le=1)
    graph_precision: float = Field(default=1.0, ge=0, le=1)
    graph_recall: float = Field(default=1.0, ge=0, le=1)
    stability_runs: int = Field(default=100, ge=1, le=100)
    coordinator_case_pass_rate: float = Field(default=1.0, ge=0, le=1)
    llm_case_pass_rate: float = Field(default=1.0, ge=0, le=1)


class CampaignConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100)
    ingestion_report_file: str
    retrieval_dataset_manifest_file: str
    vector_summary_file: str
    reranker_summary_file: str
    graph_summary_file: str
    graph_stability_file: str
    coordinator_evaluation_file: str
    coordinator_stability_file: str
    llm_evaluation_file: str
    workflow_report_file: str
    thresholds: CampaignThresholds = Field(default_factory=CampaignThresholds)


_FILE_FIELDS = (
    "ingestion_report_file",
    "retrieval_dataset_manifest_file",
    "vector_summary_file",
    "reranker_summary_file",
    "graph_summary_file",
    "graph_stability_file",
    "coordinator_evaluation_file",
    "coordinator_stability_file",
    "llm_evaluation_file",
    "workflow_report_file",
)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_campaign(path):
    source = Path(path).resolve()
    config = CampaignConfig.model_validate_json(source.read_text(encoding="utf-8-sig"))
    files = {name: (source.parent / getattr(config, name)).resolve() for name in _FILE_FIELDS}
    missing = [str(value) for value in files.values() if not value.is_file()]
    if missing:
        raise FileNotFoundError("Missing campaign evidence: " + ", ".join(missing))
    return source, config, files


def run_campaign(path):
    source, config, files = load_campaign(path)
    values = {name: _read_json(file) for name, file in files.items()}
    thresholds = config.thresholds
    checks = []
    limitations = []

    def check(stage, name, passed, actual, threshold=None):
        checks.append(
            {
                "stage": stage,
                "name": name,
                "passed": bool(passed),
                "actual": actual,
                "threshold": threshold,
            }
        )

    ingestion = values["ingestion_report_file"]
    verification = ingestion.get("verification", {})
    check("ingestion", "pipeline completed", ingestion.get("status") == "COMPLETE", ingestion.get("status"))
    check(
        "ingestion",
        "all chunks indexed and processed",
        ingestion.get("chunks", 0) > 0
        and ingestion.get("indexed_chunks") == ingestion.get("chunks")
        and ingestion.get("processed_chunks") == ingestion.get("chunks"),
        {
            "chunks": ingestion.get("chunks"),
            "indexed": ingestion.get("indexed_chunks"),
            "processed": ingestion.get("processed_chunks"),
        },
    )
    check(
        "ingestion",
        "persisted stores verified",
        verification.get("source_hashes") == "VERIFIED"
        and verification.get("qdrant_payloads_and_vectors") == "VERIFIED_AGAINST_SOURCES_AND_CACHE"
        and verification.get("neo4j_run_chunks") == ingestion.get("chunks"),
        {
            "source_hashes": verification.get("source_hashes"),
            "qdrant": verification.get("qdrant_payloads_and_vectors"),
            "neo4j_chunks": verification.get("neo4j_run_chunks"),
        },
    )
    if verification.get("semantic_verified") is not True or verification.get("coverage") == "NOT_EVALUATED":
        limitations.append(
            "Requirement extraction has quote-grounding checks, but no independently reviewed semantic precision/recall over all 121 chunks."
        )

    dataset = values["retrieval_dataset_manifest_file"]
    if dataset.get("golden_release") is not True or dataset.get("review", {}).get("approved") is not True:
        limitations.append(
            "Retrieval and graph labels are development references created by the implementation author; no independent reviewer approved them."
        )

    vector = values["vector_summary_file"]["scores"]["5"]["summary"]
    check(
        "vector", "40-query benchmark completed", vector.get("completed") == 40, vector.get("completed"), 40
    )
    check(
        "vector",
        "passage recall at five",
        vector.get("recall", 0) >= thresholds.vector_recall_at_five,
        vector.get("recall"),
        thresholds.vector_recall_at_five,
    )
    check(
        "vector",
        "required evidence recall at five",
        vector.get("mean_evidence_recall_at_k", 0) >= thresholds.vector_required_evidence_recall,
        vector.get("mean_evidence_recall_at_k"),
        thresholds.vector_required_evidence_recall,
    )
    check(
        "vector",
        "mean reciprocal rank",
        vector.get("mean_reciprocal_rank", 0) >= thresholds.vector_mrr,
        vector.get("mean_reciprocal_rank"),
        thresholds.vector_mrr,
    )
    limitations.append(
        f"Raw vector precision at five is {vector.get('precision', 0):.2%}; use the evidence selector when precision matters."
    )

    selected = values["reranker_summary_file"]["selected"]["summary"]
    check(
        "optional reranker",
        "selected evidence precision",
        selected.get("precision", 0) >= thresholds.reranker_precision,
        selected.get("precision"),
        thresholds.reranker_precision,
    )
    check(
        "optional reranker",
        "selected evidence recall",
        selected.get("recall", 0) >= thresholds.reranker_recall,
        selected.get("recall"),
        thresholds.reranker_recall,
    )

    graph = values["graph_summary_file"]
    for field, label in (("ui_ids", "UI nodes"), ("flow_ids", "flows"), ("requirement_ids", "requirements")):
        summary = graph[field]["summary"]
        check(
            "neo4j",
            f"{label} precision and recall",
            summary.get("precision", 0) >= thresholds.graph_precision
            and summary.get("recall", 0) >= thresholds.graph_recall,
            {"precision": summary.get("precision"), "recall": summary.get("recall")},
            {"precision": thresholds.graph_precision, "recall": thresholds.graph_recall},
        )
    if graph.get("checks", {}).get("fixture_only") is True:
        limitations.append(
            "Neo4j accuracy uses a synthetic contract graph; the Saleor code-to-UI-to-requirement graph is not independently labelled."
        )

    graph_stability = values["graph_stability_file"]
    check(
        "neo4j",
        "100-run query stability",
        graph_stability.get("scheduled") >= thresholds.stability_runs
        and graph_stability.get("completed") == graph_stability.get("scheduled")
        and graph_stability.get("correct") == graph_stability.get("scheduled")
        and graph_stability.get("distinct_success_outputs") == 1,
        {
            key: graph_stability.get(key)
            for key in ("scheduled", "completed", "correct", "distinct_success_outputs")
        },
        thresholds.stability_runs,
    )

    coordinator = values["coordinator_evaluation_file"]
    check(
        "coordinator",
        "golden contract cases",
        coordinator.get("status") == "PASSED"
        and coordinator.get("metrics", {}).get("case_pass_rate", 0) >= thresholds.coordinator_case_pass_rate,
        coordinator.get("metrics"),
        thresholds.coordinator_case_pass_rate,
    )
    if coordinator.get("metrics", {}).get("cases_total", 0) < 10:
        limitations.append(
            f"Coordinator accuracy covers {coordinator.get('metrics', {}).get('cases_total', 0)} fixture cases; broader PR coverage remains future work."
        )

    coordinator_stability = values["coordinator_stability_file"]
    stability_metrics = coordinator_stability.get("metrics", {})
    check(
        "coordinator",
        "100-run agent stability",
        coordinator_stability.get("status") == "PASSED"
        and stability_metrics.get("scheduled") >= thresholds.stability_runs
        and stability_metrics.get("passed") == stability_metrics.get("scheduled")
        and stability_metrics.get("distinct_behavioral_outputs") == 1,
        stability_metrics,
        thresholds.stability_runs,
    )

    llm = values["llm_evaluation_file"]
    llm_metrics = llm.get("metrics", {})
    check(
        "llm",
        "live grounding and safety",
        llm.get("status") == "PASSED"
        and llm_metrics.get("case_pass_rate", 0) >= thresholds.llm_case_pass_rate
        and llm_metrics.get("structured_output_rate") == 1
        and llm_metrics.get("grounded_finding_rate") == 1
        and llm_metrics.get("sensitive_output_leaks") == 0
        and 0 < llm_metrics.get("provider_calls", 0) <= 5,
        llm_metrics,
        thresholds.llm_case_pass_rate,
    )
    limitations.append(
        "Live LLM evaluation has four focused cases and three provider calls; it is a safety/grounding check, not a statistical model-quality claim."
    )

    workflow = values["workflow_report_file"]
    check(
        "end to end",
        "live PR workflow and behavioral verification",
        workflow.get("status") == "COMPLETED"
        and workflow.get("completeness") == "COMPLETE_FOR_CONFIGURED_SCOPE"
        and workflow.get("verification") == "COMPLETED"
        and workflow.get("runtime_attestation", {}).get("status") == "VERIFIED"
        and workflow.get("behavior_verification", {}).get("comparison", {}).get("status") == "SUPPORTED",
        {
            "status": workflow.get("status"),
            "completeness": workflow.get("completeness"),
            "verification": workflow.get("verification"),
            "attestation": workflow.get("runtime_attestation", {}).get("status"),
            "attribution": workflow.get("behavior_verification", {}).get("comparison", {}).get("status"),
        },
    )
    limitations.extend(workflow.get("gaps", []))

    all_passed = all(item["passed"] for item in checks)
    status = "PASSED_WITH_LIMITATIONS" if all_passed else "FAILED"
    return {
        "schema_version": 1,
        "status": status,
        "campaign": config.id,
        "checks": checks,
        "summary": {
            "checks_passed": sum(item["passed"] for item in checks),
            "checks_total": len(checks),
            "limitations": len(dict.fromkeys(limitations)),
        },
        "limitations": list(dict.fromkeys(limitations)),
        "inputs": {
            name: {
                "path": str(file),
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
            for name, file in files.items()
        }
        | {"config": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}},
    }


def campaign_markdown(report):
    lines = [
        "# Final evaluation campaign",
        "",
        f"Status: **{report['status']}**",
        "",
        f"Campaign: `{report['campaign']}`",
        "",
        f"Passed {report['summary']['checks_passed']} of {report['summary']['checks_total']} machine checks.",
        "",
        "| Stage | Check | Result | Measured | Threshold |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["checks"]:
        actual = json.dumps(item["actual"], sort_keys=True).replace("|", "\\|")
        threshold = json.dumps(item["threshold"], sort_keys=True).replace("|", "\\|")
        lines.append(
            f"| {item['stage']} | {item['name']} | {'PASS' if item['passed'] else 'FAIL'} | "
            f"`{actual}` | `{threshold}` |"
        )
    lines.extend(["", "## Limits on the claim", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The measured pipeline contracts pass for the configured Saleor voucher scenario. "
            "The limitations above prevent a claim of general production accuracy across arbitrary repositories and PRs.",
            "",
        ]
    )
    return "\n".join(lines)


def campaign_schema():
    return {
        **CampaignConfig.model_json_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Final evaluation campaign",
    }
