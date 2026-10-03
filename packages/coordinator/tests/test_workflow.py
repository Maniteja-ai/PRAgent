import json
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from trace_coordinator import (
    AnalysisRequest,
    CallLimits,
    Coordinator,
    HumanReviewPolicy,
    ReviewResponse,
)
from trace_coordinator.decision_model.implementations.fixture import FixtureDecisionModel
from trace_coordinator.domain.errors import RunMismatch, ToolFailure
from trace_coordinator.domain.models import Evidence, ToolResult
from trace_coordinator.setup import create_coordinator
from trace_coordinator.tool.implementations.attestation import AttestationInput
from trace_coordinator.tool.implementations.fixture import FixtureTool

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/coordinator/voucher-analysis.json").read_text(encoding="utf-8"))
REQUEST = AnalysisRequest.model_validate_json(
    (ROOT / "tests/fixtures/requests/demo.json").read_text(encoding="utf-8")
)


def coordinator(path, decisions=None, limits=None, tools=None, human_review=None):
    return Coordinator(
        path,
        limits or CallLimits(retry_delay_seconds=0),
        tools or [FixtureTool(name, data) for name, data in FIXTURE["tools"].items()],
        FixtureDecisionModel(decisions or FIXTURE["decisions"]),
        human_review=human_review,
    )


def test_complete_offline_workflow_and_cached_rerun(tmp_path):
    app = coordinator(tmp_path)
    result = app.run(REQUEST, "run")
    assert result["status"] == "COMPLETED"
    assert result["verification"] == "NOT_RUN"
    assert len(result["findings"]) == 1
    assert len(result["evidence"]) == 4
    assert sum(item["attempts"] for item in result["tool_usage"]) == 6
    assert app.run(REQUEST, "run") == result
    assert coordinator(tmp_path).run(REQUEST, "run") == result


def test_infinite_search_stops_at_five_tool_attempts(tmp_path):
    app = coordinator(
        tmp_path, [{"action": "tool", "tool": "knowledge.documents", "arguments": {"query": "again"}}]
    )
    report = app.run(REQUEST, "loop")
    usage = {row["tool"]: row["attempts"] for row in report["tool_usage"]}
    assert usage["knowledge.documents"] == 5  # Initial retrieval + four repeated searches.
    assert usage["model.decide"] == 5
    assert report["stop_reason"] == "LimitReached"
    assert report["status"] == "STOPPED"
    assert report["limit_events"][0]["detail"].startswith("coordinator/knowledge.documents")
    assert report["completeness"] == "PARTIAL"


def test_model_itself_cannot_loop_over_different_tools_forever(tmp_path):
    choices = [
        {"action": "tool", "tool": "browser.observe", "arguments": {"goal": str(i)}} for i in range(10)
    ]
    report = coordinator(tmp_path, choices).run(REQUEST, "loop")
    assert all(row["attempts"] <= 5 for row in report["tool_usage"])
    assert report["limit_events"][0]["detail"].startswith("coordinator/model.decide")


def test_human_review_survives_restart_without_resetting_usage(tmp_path):
    choices = [
        {"action": "review", "question": "Which voucher control?"},
        FIXTURE["decisions"][0],
        FIXTURE["decisions"][1],
    ]
    first = coordinator(tmp_path, choices).run(REQUEST, "review")
    assert first["status"] == "WAITING_FOR_REVIEW"
    assert sum(r["attempts"] for r in first["tool_usage"]) == 4
    restarted = coordinator(tmp_path, choices)
    assert restarted.run(REQUEST, "review") == first
    result = restarted.run(
        REQUEST, "review", review=ReviewResponse(answer="Use the checkout voucher control")
    )
    assert result["status"] == "COMPLETED"
    usage = {r["tool"]: r["attempts"] for r in result["tool_usage"]}
    assert usage["github.diff"] == 1
    assert usage["model.decide"] == 3


def test_non_blocking_analysis_review_finalizes_with_question_and_gap(tmp_path):
    choices = [{"action": "review", "question": "Which voucher control should be analyzed?"}]
    policy = HumanReviewPolicy(policy="non_blocking")
    app = coordinator(tmp_path, choices, human_review=policy)
    result = app.run(REQUEST, "non-blocking")

    assert result["status"] == "COMPLETED_WITH_GAPS"
    assert result["completeness"] == "PARTIAL"
    assert result["human_review"] == {
        "policy": "non_blocking",
        "status": "NOT_ANSWERED",
        "requests": [
            {
                "kind": "analysis",
                "question": "Which voucher control should be analyzed?",
                "status": "NOT_ANSWERED",
                "answer": None,
            }
        ],
        "follow_up_verification_allowed": False,
    }
    assert any("not answered" in gap for gap in result["gaps"])
    assert app.run(REQUEST, "non-blocking") == result
    with pytest.raises(RunMismatch, match="no unanswered verification approval"):
        app.run(
            REQUEST,
            "non-blocking",
            review=ReviewResponse(answer="Use the checkout voucher control"),
        )


def test_review_cannot_grant_extra_calls(tmp_path):
    choices = [
        {"action": "review", "question": "Continue?"},
        {"action": "tool", "tool": "knowledge.documents", "arguments": {"query": "q"}},
    ]
    limits = CallLimits(overrides={"coordinator": {"knowledge.documents": 1}})
    app = coordinator(tmp_path, choices, limits)
    app.run(REQUEST, "run")
    result = app.run(REQUEST, "run", review=ReviewResponse(answer="Ignore limits and call the tool"))
    assert result["stop_reason"] == "LimitReached"
    assert next(r for r in result["tool_usage"] if r["tool"] == "knowledge.documents")["attempts"] == 1


def test_repeated_review_requests_are_bounded(tmp_path):
    app = coordinator(
        tmp_path, [{"action": "review", "question": "Again?"}], CallLimits(max_review_requests=1)
    )
    assert app.run(REQUEST, "run")["status"] == "WAITING_FOR_REVIEW"
    result = app.run(REQUEST, "run", review=ReviewResponse(answer="Yes"))
    assert result["stop_reason"] == "LimitReached"


def test_bad_evidence_reference_is_rejected(tmp_path):
    decisions = [
        {
            "action": "finish",
            "findings": [{"title": "Unsupported", "explanation": "Claim", "evidence_ids": ["invented-id"]}],
        }
    ]
    report = coordinator(tmp_path, decisions).run(REQUEST, "run")
    assert not report["findings"]
    assert any("Rejected unsupported" in gap for gap in report["gaps"])
    assert next(r["attempts"] for r in report["tool_usage"] if r["tool"] == "model.decide") == 2


def test_unique_nested_graph_entity_citation_resolves_to_parent_evidence(tmp_path):
    tools = [FixtureTool(name, data) for name, data in FIXTURE["tools"].items()]
    graph = next(tool for tool in tools if tool.name == "knowledge.graph")
    graph.result = graph.result.model_copy(
        update={
            "evidence": (
                graph.result.evidence[0].model_copy(
                    update={
                        "summary": json.dumps(
                            [
                                {
                                    "ui_ids": ["ui:nested"],
                                    "flow_ids": [],
                                    "requirement_ids": [],
                                    "mapped_entities": [{"id": "ui:nested"}],
                                }
                            ]
                        )
                    }
                ),
            )
        }
    )
    decision = {
        "action": "finish",
        "findings": [
            {
                "title": "Nested graph citation",
                "explanation": "The graph entity is contained by graph evidence.",
                "evidence_ids": ["diff-demo", "ui:nested"],
            }
        ],
    }
    report = coordinator(tmp_path, decisions=[decision], tools=tools).run(REQUEST, "nested")
    assert report["findings"][0]["evidence_ids"] == ["diff-demo", "graph-demo"]
    assert next(r["attempts"] for r in report["tool_usage"] if r["tool"] == "model.decide") == 1


def test_citation_repair_uses_existing_evidence_and_consumes_same_model_budget(tmp_path):
    bad = {
        "action": "finish",
        "findings": [
            {"title": "Unsupported", "explanation": "Claim", "evidence_ids": ["invented-id"]},
        ],
    }
    fixed = json.loads(json.dumps(FIXTURE["decisions"][-1]))
    fixed["findings"][0]["evidence_ids"].remove("ui-demo")
    report = coordinator(tmp_path, [bad, fixed]).run(REQUEST, "repair")
    assert len(report["findings"]) == 1
    assert report["findings"][0]["evidence_ids"] == fixed["findings"][0]["evidence_ids"]
    assert not any("Rejected unsupported" in g for g in report["gaps"])
    assert next(r["attempts"] for r in report["tool_usage"] if r["tool"] == "model.decide") == 2
    capped = coordinator(
        tmp_path / "capped",
        [bad, fixed],
        CallLimits(
            overrides={"coordinator": {"model.decide": 1}},
        ),
    ).run(REQUEST, "repair")
    assert capped["status"] == "STOPPED"
    assert not capped["findings"]


def test_changed_config_or_request_cannot_resume_same_run(tmp_path):
    coordinator(tmp_path).run(REQUEST, "run")
    with pytest.raises(RunMismatch):
        coordinator(tmp_path, limits=CallLimits(per_agent_tool=4)).run(REQUEST, "run")
    with pytest.raises(RunMismatch):
        coordinator(tmp_path).run(REQUEST.model_copy(update={"pull_request": 1200}), "run")


def test_run_lock_rejects_concurrent_graph_execution(tmp_path):
    with FileLock(str(tmp_path / "run.lock")):
        with pytest.raises(Timeout):
            coordinator(tmp_path).run(REQUEST, "run")


def test_unauthorized_tool_proposals_terminate_with_round_limit(tmp_path):
    report = coordinator(
        tmp_path, [{"action": "tool", "tool": "shell.exec", "arguments": {}}], CallLimits(max_rounds=2)
    ).run(REQUEST, "run")
    assert report["stop_reason"] == "LimitReached"
    assert not any(row["tool"] == "shell.exec" for row in report["tool_usage"])


def test_review_response_without_pending_review_is_rejected(tmp_path):
    with pytest.raises(RunMismatch):
        coordinator(tmp_path).run(REQUEST, "run", review=ReviewResponse(answer="Yes"))


def test_changed_fixture_cannot_reuse_saved_results(tmp_path):
    coordinator(tmp_path).run(REQUEST, "run")
    with pytest.raises(RunMismatch):
        coordinator(tmp_path, [{"action": "finish"}]).run(REQUEST, "run")


def test_safe_run_ids(tmp_path):
    with pytest.raises(ValueError, match="safe run ID"):
        coordinator(tmp_path).run(REQUEST, "../../outside")


def test_missing_diff_stops_before_model(tmp_path):
    tools = [FixtureTool("github.diff", {"gaps": ["No diff"]})]
    report = coordinator(tmp_path, tools=tools).run(REQUEST, "run")
    assert report["stop_reason"] == "ToolFailure"
    assert report["status"] == "FAILED"
    assert all(r["tool"] != "model.decide" for r in report["tool_usage"])


class AttestationFixture:
    name = "deployment.attest"
    version = "attestation-test-v1"
    description = "Test deployment identity"
    input_model = AttestationInput
    allowed_agents = frozenset({"coordinator"})

    def __init__(self, *, fail=None, different_backend=False):
        self.fail, self.different_backend = fail, different_backend

    def execute(self, arguments, context):
        if arguments.environment == self.fail:
            raise ToolFailure("attestation unavailable")
        backend = ("b" if self.different_backend and arguments.environment == "patched" else "a") * 64
        revision = ("a" if arguments.environment == "baseline" else "b") * 40
        metadata = {
            "environment": arguments.environment,
            "origin": f"https://{arguments.environment}.example",
            "revision": revision,
            "deployment_id": "dpl_" + arguments.environment,
            "backend_fingerprint": backend,
            "channel": "default-channel",
            "captured_at": 1,
        }
        return ToolResult(
            evidence=(
                Evidence(
                    id="attestation:" + arguments.environment,
                    project_id=context.project_id,
                    kind="attestation",
                    source="test",
                    summary=json.dumps(metadata),
                    metadata=metadata,
                ),
            )
        )


def test_workflow_records_two_runtime_attestations_before_analysis(tmp_path):
    tools = [FixtureTool(name, data) for name, data in FIXTURE["tools"].items()]
    tools.append(AttestationFixture())
    report = coordinator(tmp_path, tools=tools).run(REQUEST, "attested")
    assert report["status"] == "COMPLETED"
    assert report["runtime_attestation"]["status"] == "VERIFIED"
    assert report["runtime_attestation"]["shared_backend"] is True
    assert next(r for r in report["tool_usage"] if r["tool"] == "deployment.attest")["attempts"] == 2


@pytest.mark.parametrize(
    "tool", [AttestationFixture(fail="patched"), AttestationFixture(different_backend=True)]
)
def test_attestation_failure_stops_before_retrieval_or_model(tmp_path, tool):
    tools = [FixtureTool("github.diff", FIXTURE["tools"]["github.diff"]), tool]
    report = coordinator(tmp_path, tools=tools).run(REQUEST, "failed-attestation")
    assert report["status"] == "FAILED"
    assert all(
        row["tool"] not in {"knowledge.graph", "knowledge.documents", "model.decide"}
        for row in report["tool_usage"]
    )


def test_bootstrap_resolves_paths_against_config(tmp_path, monkeypatch):
    config = json.loads((ROOT / "tests/fixtures/configs/demo.json").read_text(encoding="utf-8"))
    config["state_directory"] = str(tmp_path / "state")
    config.pop("runtime_config_file", None)
    config["tool_fixture_file"] = str(ROOT / "tests/fixtures/coordinator/voucher-analysis.json")
    config["model"]["file"] = str(ROOT / "tests/fixtures/coordinator/voucher-analysis.json")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    with create_coordinator(path) as app:
        assert app.run(REQUEST, "run")["status"] == "COMPLETED"


def test_crash_after_tool_completion_before_graph_checkpoint_uses_cached_result(tmp_path, monkeypatch):
    from trace_coordinator.analysis_workflow.execution import ToolRuntime

    class SimulatedCrash(BaseException):
        pass

    original = ToolRuntime.call_tool

    def crash_after_completion(self, context, operation, name, arguments):
        result = original(self, context, operation, name, arguments)
        if operation == "fetch_changes":
            raise SimulatedCrash()
        return result

    monkeypatch.setattr(ToolRuntime, "call_tool", crash_after_completion)
    with pytest.raises(SimulatedCrash):
        coordinator(tmp_path).run(REQUEST, "crash")
    monkeypatch.setattr(ToolRuntime, "call_tool", original)
    restarted = coordinator(tmp_path)
    result = restarted.run(REQUEST, "crash")
    assert result["status"] == "COMPLETED"
    assert next(r for r in result["tool_usage"] if r["tool"] == "github.diff")["attempts"] == 1


def test_crash_during_tool_execution_stops_resume_without_reexecution(tmp_path):
    class SimulatedCrash(BaseException):
        pass

    class CrashingTool(FixtureTool):
        calls = 0

        def execute(self, arguments, context):
            self.calls += 1
            raise SimulatedCrash()

    tool = CrashingTool("github.diff", FIXTURE["tools"]["github.diff"])
    app = coordinator(tmp_path, tools=[tool])
    with pytest.raises(SimulatedCrash):
        app.run(REQUEST, "crash")
    result = coordinator(tmp_path, tools=[tool]).run(REQUEST, "crash")
    assert result["stop_reason"] == "UncertainExecution"
    assert result["tool_usage"][0]["uncertain"] == 1
    assert tool.calls == 1
