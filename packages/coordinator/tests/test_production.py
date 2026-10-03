import json
from pathlib import Path

import jsonschema

from trace_coordinator.evaluation.campaign import CampaignConfig, campaign_schema, run_campaign
from trace_coordinator.evaluation.coordinator import (
    GoldenDataset,
    dataset_schema,
    evaluate_dataset,
    evaluate_stability,
)
from trace_coordinator.evaluation.readiness import ProductionGateConfig, gate_schema, run_gate
from trace_coordinator.infrastructure.artifacts import save_artifact

ROOT = Path(__file__).resolve().parents[1]


def test_reviewed_golden_dataset_runs_full_coordinator_contract(tmp_path):
    dataset = ROOT / "evaluation/coordinator-golden-v1.json"
    report = evaluate_dataset(dataset, tmp_path / "evaluation")
    assert report["status"] == "PASSED"
    assert report["metrics"] == {
        "true_positive": 2,
        "false_positive": 0,
        "false_negative": 0,
        "precision": 1.0,
        "recall": 1.0,
        "case_pass_rate": 1.0,
        "cases_passed": 5,
        "cases_total": 5,
    }
    assert all(item["citations_valid"] and item["passed"] for item in report["cases"])


def test_golden_evaluation_fails_when_expected_finding_is_missed(tmp_path):
    raw = json.loads((ROOT / "evaluation/coordinator-golden-v1.json").read_text())
    raw["cases"][0]["expected"]["findings"][0]["title"] = "A different required finding"
    selected = tmp_path / "bad.json"
    selected.write_text(json.dumps(raw), encoding="utf-8")
    report = evaluate_dataset(selected, tmp_path / "evaluation")
    assert report["status"] == "FAILED"
    assert report["metrics"]["precision"] < 1 and report["metrics"]["recall"] < 1


def test_coordinator_stability_repeats_the_complete_contract(tmp_path):
    dataset = ROOT / "evaluation/coordinator-golden-v1.json"
    report = evaluate_stability(dataset, tmp_path / "stability", repetitions=3)
    assert report["status"] == "PASSED"
    assert report["metrics"]["scheduled"] == 3
    assert report["metrics"]["passed"] == 3
    assert report["metrics"]["distinct_behavioral_outputs"] == 1


def test_coordinator_stability_rejects_unsafe_repeat_count(tmp_path):
    dataset = ROOT / "evaluation/coordinator-golden-v1.json"
    for repetitions in (0, 101):
        try:
            evaluate_stability(dataset, tmp_path / str(repetitions), repetitions=repetitions)
        except ValueError as error:
            assert "between 1 and 100" in str(error)
        else:
            raise AssertionError("unsafe repetition count was accepted")


def readiness_fixture(tmp_path):
    artifact = save_artifact(tmp_path, "run", b"evidence", ".json")
    report = {
        "status": "COMPLETED",
        "completeness": "COMPLETE_FOR_CONFIGURED_SCOPE",
        "verification": "COMPLETED",
        "model_guardrails": {
            "status": "PASSED",
            "events": [
                {"kind": "MODEL_GUARDRAIL", "detail": "input"},
                {"kind": "MODEL_GUARDRAIL", "detail": "output"},
            ],
            "policy_fingerprint": "b" * 64,
        },
        "runtime_attestation": {
            "status": "VERIFIED",
            "shared_backend": True,
            "deployments": {"baseline": {}, "patched": {}},
        },
        "verification_approval": {
            "mode": "preapproved",
            "approved": True,
            "scenario_id": "voucher",
            "policy_fingerprint": "a" * 64,
        },
        "behavior_verification": {
            "comparison": {"status": "SUPPORTED"},
            "checks": [{"environment": "patched", "name": "Apply", "status": "PASS", "evidence_ids": ["e1"]}],
            "requirements": [{"id": "REQ_APPLY", "status": "PASS", "evidence_ids": ["e1"]}],
        },
        "findings": [{"title": "Impact", "evidence_ids": ["e1"]}],
        "evidence": {
            "e1": {
                "id": "e1",
                "project_id": "p",
                "kind": "verification",
                "summary": "evidence",
                "source": "test",
                "metadata": {"artifact": artifact},
            }
        },
        "tool_usage": [{"agent": "coordinator", "tool": "model.decide", "attempts": 1, "uncertain": 0}],
        "limit_events": [],
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "evaluation.json").write_text(
        json.dumps({"status": "PASSED", "metrics": {"precision": 1, "recall": 1}}), encoding="utf-8"
    )
    (tmp_path / "llm-evaluation.json").write_text(
        json.dumps(
            {
                "status": "PASSED",
                "metrics": {
                    "case_pass_rate": 1,
                    "structured_output_rate": 1,
                    "grounded_finding_rate": 1,
                    "sensitive_output_leaks": 0,
                    "provider_calls": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "junit.xml").write_text(
        '<testsuite tests="210" failures="0" errors="0" skipped="0"/>', encoding="utf-8"
    )
    (tmp_path / "coverage.json").write_text(json.dumps({"totals": {"percent_covered": 95}}), encoding="utf-8")
    config = {
        "report_file": "report.json",
        "evaluation_report_file": "evaluation.json",
        "llm_evaluation_report_file": "llm-evaluation.json",
        "junit_file": "junit.xml",
        "coverage_file": "coverage.json",
        "minimum_tests": 200,
        "minimum_coverage_percent": 90,
        "required_behavior_checks": {"patched::Apply": "PASS"},
        "required_requirements": {"REQ_APPLY": "PASS"},
    }
    (tmp_path / "gate.json").write_text(json.dumps(config), encoding="utf-8")
    return tmp_path / "gate.json", report


def test_release_gate_verifies_tests_evaluation_attestation_and_artifacts(tmp_path):
    config, _ = readiness_fixture(tmp_path)
    result = run_gate(config)
    assert result["status"] == "READY"
    assert all(item["passed"] for item in result["checks"])


def test_release_gate_fails_closed_on_uncertain_tool_attempt(tmp_path):
    config, report = readiness_fixture(tmp_path)
    report["tool_usage"][0]["uncertain"] = 1
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    result = run_gate(config)
    assert result["status"] == "NOT_READY"
    assert (
        next(item for item in result["checks"] if item["name"] == "tool attempt ceilings")["passed"] is False
    )


def test_release_gate_fails_when_approval_is_not_bound_to_policy(tmp_path):
    config, report = readiness_fixture(tmp_path)
    report["verification_approval"]["policy_fingerprint"] = ""
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    result = run_gate(config)
    assert result["status"] == "NOT_READY"
    assert (
        next(item for item in result["checks"] if item["name"] == "verification approval bound to policy")[
            "passed"
        ]
        is False
    )


def test_production_json_schemas_match_runtime_and_examples():
    for schema_value, model, example in (
        (dataset_schema(), GoldenDataset, ROOT / "evaluation/coordinator-golden-v1.json"),
        (gate_schema(), ProductionGateConfig, None),
        (
            campaign_schema(),
            CampaignConfig,
            ROOT / "configs/evaluation/final-campaign.json",
        ),
    ):
        jsonschema.Draft202012Validator.check_schema(schema_value)
        if example:
            value = json.loads(example.read_text())
            jsonschema.validate(value, schema_value)
            model.model_validate(value)
    assert json.loads((ROOT / "schemas/golden-dataset.schema.json").read_text()) == dataset_schema()
    assert json.loads((ROOT / "schemas/production-gate.schema.json").read_text()) == gate_schema()
    assert json.loads((ROOT / "schemas/evaluation-campaign.schema.json").read_text()) == campaign_schema()


def test_saved_final_campaign_is_machine_checkable():
    report = run_campaign(ROOT / "configs/evaluation/final-campaign.json")
    assert report["status"] == "PASSED_WITH_LIMITATIONS"
    assert report["summary"]["checks_passed"] == report["summary"]["checks_total"]
    assert report["limitations"]
