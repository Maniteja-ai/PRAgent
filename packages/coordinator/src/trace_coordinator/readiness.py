"""Machine-checkable production release gate."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from pydantic import Field

from trace_coordinator.mapping import verified_bytes
from trace_coordinator.models import Record


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


def load_gate(path):
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


def _artifact_references(value):
    if isinstance(value, dict):
        if {"path", "sha256", "bytes"} <= value.keys():
            yield value
        for child in value.values():
            yield from _artifact_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _artifact_references(child)


def run_gate(path):
    config, files = load_gate(path)
    report = json.loads(files["report_file"].read_text(encoding="utf-8"))
    evaluation = json.loads(files["evaluation_report_file"].read_text(encoding="utf-8"))
    llm_evaluation = json.loads(files["llm_evaluation_report_file"].read_text(encoding="utf-8"))
    coverage = json.loads(files["coverage_file"].read_text(encoding="utf-8"))
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
    checks = []

    def gate(name, passed, actual):
        checks.append({"name": name, "passed": bool(passed), "actual": actual})

    gate("workflow completed", report.get("status") == "COMPLETED", report.get("status"))
    gate(
        "configured scope complete",
        report.get("completeness") == "COMPLETE_FOR_CONFIGURED_SCOPE",
        report.get("completeness"),
    )
    model_guardrails = report.get("model_guardrails", {})
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
    attestation = report.get("runtime_attestation", {})
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
    approval = report.get("verification_approval", {})
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
        report.get("behavior_verification", {}).get("comparison", {}).get("status") == "SUPPORTED",
        report.get("behavior_verification", {}).get("comparison", {}).get("status"),
    )
    actual_checks = {
        f"{item['environment']}::{item['name']}": item["status"]
        for item in report.get("behavior_verification", {}).get("checks", [])
    }
    gate(
        "required behavior outcomes",
        all(actual_checks.get(k) == v for k, v in config.required_behavior_checks.items()),
        actual_checks,
    )
    actual_requirements = {
        item["id"]: item["status"] for item in report.get("behavior_verification", {}).get("requirements", [])
    }
    gate(
        "required requirement outcomes",
        all(actual_requirements.get(k) == v for k, v in config.required_requirements.items()),
        actual_requirements,
    )
    gate("golden evaluation", evaluation.get("status") == "PASSED", evaluation.get("metrics"))
    llm_metrics = llm_evaluation.get("metrics", {})
    gate(
        "live LLM safety and grounding evaluation",
        llm_evaluation.get("status") == "PASSED"
        and llm_metrics.get("case_pass_rate") == 1
        and llm_metrics.get("structured_output_rate") == 1
        and llm_metrics.get("grounded_finding_rate") == 1
        and llm_metrics.get("sensitive_output_leaks") == 0
        and 0 < llm_metrics.get("provider_calls", 0) <= 5,
        llm_metrics,
    )
    gate(
        "test suite",
        tests >= config.minimum_tests and failures == 0 and errors == 0,
        {"tests": tests, "failures": failures, "errors": errors},
    )
    percent = float(coverage["totals"]["percent_covered"])
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
            *report.get("behavior_verification", {}).get("checks", []),
            *report.get("behavior_verification", {}).get("requirements", []),
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
    return {
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


def readiness_markdown(report):
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
    lines.extend(f"| {item['name']} | {'PASS' if item['passed'] else 'FAIL'} |" for item in report["checks"])
    return "\n".join(lines) + "\n"


def gate_schema():
    return {
        **ProductionGateConfig.model_json_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Coordinator production gate",
    }
