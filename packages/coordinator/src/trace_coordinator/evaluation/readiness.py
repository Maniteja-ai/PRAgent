"""Machine-checkable production release gate."""

import hashlib
import json
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from pydantic import Field, TypeAdapter

from trace_coordinator.domain.contracts import (
    ArtifactPayload,
    JsonObject,
    as_json_object,
    as_json_value,
)
from trace_coordinator.domain.models import Record
from trace_coordinator.ui_analysis.mapping import verified_bytes


class ProductionGateConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    report_file: str
    evaluation_report_file: str
    llm_evaluation_report_file: str
    junit_file: str
    coverage_file: str
    minimum_tests: int = Field(default=200, ge=1)
    minimum_coverage_percent: float = Field(default=90, ge=0, le=100)
    required_behavior_checks: dict[str, Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN"]]
    required_requirements: dict[str, Literal["PASS", "FAIL", "BLOCKED"]] = Field(default_factory=dict)


class GateCheckPayload(TypedDict):
    name: str
    passed: bool
    actual: object


class GateEvidenceReference(TypedDict, total=False):
    id: str
    environment: str
    name: str
    status: str
    evidence_ids: list[str]


class GateRequirementReference(GateEvidenceReference, total=False):
    pass


class GateCallUsage(TypedDict):
    agent: str
    tool: str
    attempts: int
    uncertain: int


class GateAuditEvent(TypedDict, total=False):
    kind: str
    detail: str
    created: float


class GateGuardrails(TypedDict):
    status: str
    events: list[GateAuditEvent]
    policy_fingerprint: str


class GateAttestation(TypedDict):
    status: str
    shared_backend: bool
    deployments: JsonObject


class GateApproval(TypedDict):
    approved: bool
    mode: str
    scenario_id: str | None
    policy_fingerprint: str


class GateComparison(TypedDict):
    status: str
    reason: NotRequired[str]


class GateBehavior(TypedDict):
    comparison: GateComparison
    checks: list[GateEvidenceReference]
    requirements: list[GateRequirementReference]


class ProductionReportPayload(TypedDict):
    status: str
    completeness: str
    verification: str
    model_guardrails: GateGuardrails
    runtime_attestation: GateAttestation
    verification_approval: GateApproval
    behavior_verification: GateBehavior
    tool_usage: list[GateCallUsage]
    limit_events: list[JsonObject]
    evidence: dict[str, JsonObject]
    findings: list[GateEvidenceReference]


_REPORT_ADAPTER = TypeAdapter(ProductionReportPayload)
_ARTIFACT_ADAPTER = TypeAdapter(ArtifactPayload)


def load_gate(path: str | Path) -> tuple[ProductionGateConfig, dict[str, Path]]:
    source = Path(path).resolve()
    config = ProductionGateConfig.model_validate_json(source.read_text(encoding="utf-8-sig"))
    return config, {
        field: (source.parent / getattr(config, field)).resolve()
        for field in (
            "report_file",
            "evaluation_report_file",
            "llm_evaluation_report_file",
            "junit_file",
            "coverage_file",
        )
    }


def _artifact_references(value: object) -> Iterator[ArtifactPayload]:
    if isinstance(value, dict):
        if {"path", "sha256", "bytes"} <= value.keys():
            yield _ARTIFACT_ADAPTER.validate_python(value)
        for child in value.values():
            yield from _artifact_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _artifact_references(child)


def run_gate(path: str | Path) -> JsonObject:
    config, files = load_gate(path)
    report = _REPORT_ADAPTER.validate_json(files["report_file"].read_text(encoding="utf-8"))
    evaluation = as_json_object(json.loads(files["evaluation_report_file"].read_text(encoding="utf-8")))
    llm_evaluation = as_json_object(
        json.loads(files["llm_evaluation_report_file"].read_text(encoding="utf-8"))
    )
    coverage = as_json_object(json.loads(files["coverage_file"].read_text(encoding="utf-8")))
    root = ET.parse(files["junit_file"]).getroot()
    tests = int(
        root.attrib.get("tests", sum(int(n.attrib.get("tests", 0)) for n in root.findall("testsuite")))
    )
    failures = int(
        root.attrib.get("failures", sum(int(n.attrib.get("failures", 0)) for n in root.findall("testsuite")))
    )
    errors = int(
        root.attrib.get("errors", sum(int(n.attrib.get("errors", 0)) for n in root.findall("testsuite")))
    )
    checks: list[GateCheckPayload] = []

    def gate(name: str, passed: object, actual: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "actual": as_json_value(actual)})

    gate("workflow completed", report.get("status") == "COMPLETED", report.get("status"))
    gate(
        "configured scope complete",
        report.get("completeness") == "COMPLETE_FOR_CONFIGURED_SCOPE",
        report.get("completeness"),
    )
    model_guardrails = report["model_guardrails"]
    guardrail_fingerprint = model_guardrails.get("policy_fingerprint", "")
    guardrail_events = model_guardrails.get("events", [])
    gate(
        "model guardrails applied",
        model_guardrails.get("status") in {"PASSED", "SANITIZED"}
        and len(guardrail_events) >= 2
        and {event.get("kind") for event in guardrail_events} == {"MODEL_GUARDRAIL"}
        and len(guardrail_fingerprint) == 64
        and all(character in "0123456789abcdef" for character in guardrail_fingerprint),
        {
            "status": model_guardrails.get("status"),
            "events": len(guardrail_events),
            "policy_fingerprint": guardrail_fingerprint,
        },
    )
    attestation = report["runtime_attestation"]
    gate(
        "two runtime builds attested",
        attestation.get("status") == "VERIFIED"
        and attestation.get("shared_backend") is True
        and set(attestation.get("deployments", {})) == {"baseline", "patched"},
        attestation.get("status"),
    )
    gate(
        "behavior verification completed",
        report.get("verification") == "COMPLETED",
        report.get("verification"),
    )
    approval = report["verification_approval"]
    approval_fingerprint = approval.get("policy_fingerprint", "")
    gate(
        "verification approval bound to policy",
        approval.get("approved") is True
        and approval.get("mode") in {"preapproved", "human_review"}
        and bool(approval.get("scenario_id"))
        and len(approval_fingerprint) == 64
        and all(character in "0123456789abcdef" for character in approval_fingerprint),
        {
            "approved": approval.get("approved"),
            "mode": approval.get("mode"),
            "scenario_id": approval.get("scenario_id"),
            "policy_fingerprint": approval_fingerprint,
        },
    )
    gate(
        "behavior attribution supported",
        report["behavior_verification"].get("comparison", {}).get("status") == "SUPPORTED",
        report["behavior_verification"].get("comparison", {}).get("status"),
    )
    actual_checks = {
        f"{item['environment']}::{item['name']}": item["status"]
        for item in report["behavior_verification"].get("checks", [])
    }
    gate(
        "required behavior outcomes",
        all(actual_checks.get(k) == v for k, v in config.required_behavior_checks.items()),
        actual_checks,
    )
    actual_requirements = {
        item["id"]: item["status"] for item in report["behavior_verification"].get("requirements", [])
    }
    gate(
        "required requirement outcomes",
        all(actual_requirements.get(k) == v for k, v in config.required_requirements.items()),
        actual_requirements,
    )
    gate("golden evaluation", evaluation.get("status") == "PASSED", evaluation.get("metrics"))
    llm_metrics = as_json_object(llm_evaluation.get("metrics", {}))
    provider_calls = llm_metrics.get("provider_calls", 0)
    provider_calls_valid = (
        isinstance(provider_calls, int) and not isinstance(provider_calls, bool) and 0 < provider_calls <= 5
    )
    gate(
        "live LLM safety and grounding evaluation",
        llm_evaluation.get("status") == "PASSED"
        and llm_metrics.get("case_pass_rate") == 1
        and llm_metrics.get("structured_output_rate") == 1
        and llm_metrics.get("grounded_finding_rate") == 1
        and llm_metrics.get("sensitive_output_leaks") == 0
        and provider_calls_valid,
        llm_metrics,
    )
    gate(
        "test suite",
        tests >= config.minimum_tests and failures == 0 and errors == 0,
        {"tests": tests, "failures": failures, "errors": errors},
    )
    coverage_totals = as_json_object(coverage["totals"])
    covered_value = coverage_totals["percent_covered"]
    if isinstance(covered_value, bool) or not isinstance(covered_value, (int, float, str)):
        raise ValueError("Coverage percentage is invalid")
    percent = float(covered_value)
    gate("coverage", percent >= config.minimum_coverage_percent, percent)
    usage = report.get("tool_usage", [])
    gate(
        "tool attempt ceilings",
        bool(usage) and all(row["attempts"] <= 5 and row["uncertain"] == 0 for row in usage),
        usage,
    )
    gate("no limit events", not report.get("limit_events"), len(report.get("limit_events", [])))
    evidence_ids = set(report.get("evidence", {}))
    citations = [
        ref
        for item in [
            *report.get("findings", []),
            *report["behavior_verification"].get("checks", []),
            *report["behavior_verification"].get("requirements", []),
        ]
        for ref in item.get("evidence_ids", [])
    ]
    gate("evidence citations", bool(citations) and set(citations) <= evidence_ids, len(citations))
    artifacts = list(_artifact_references(report))
    try:
        for reference in artifacts:
            verified_bytes(reference)
    except (OSError, ValueError):
        artifacts_valid = False
    else:
        artifacts_valid = bool(artifacts)
    gate("evidence artifact hashes", artifacts_valid, len(artifacts))
    status = "READY" if all(item["passed"] for item in checks) else "NOT_READY"
    return as_json_object(
        {
            "schema_version": 1,
            "status": status,
            "scope": "Configured Saleor PR impact analysis and voucher behavior scenario",
            "checks": checks,
            "inputs": {
                name: {
                    "path": str(file),
                    "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                }
                for name, file in files.items()
            },
        }
    )


def readiness_markdown(report: JsonObject) -> str:
    lines = [
        "# Production readiness gate",
        "",
        f"Status: {report['status']}",
        "",
        f"Scope: {report['scope']}",
        "",
        "| Gate | Result |",
        "| --- | --- |",
    ]
    check_values = report.get("checks")
    if not isinstance(check_values, list):
        raise ValueError("Readiness checks are invalid")
    for value in check_values:
        item = as_json_object(value)
        lines.append(f"| {item['name']} | {'PASS' if item['passed'] else 'FAIL'} |")
    return "\n".join(lines) + "\n"


def gate_schema() -> JsonObject:
    return as_json_object(
        {
            **ProductionGateConfig.model_json_schema(),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Coordinator production gate",
        }
    )
