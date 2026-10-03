import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_verification import World

from trace_coordinator import AnalysisRequest, CallLimits, Coordinator, ReviewResponse
from trace_coordinator.adapters.fixtures import FixtureModel, FixtureTool
from trace_coordinator.adapters.voucher_verification import VoucherScenario
from trace_coordinator.application import ApplicationConfig
from trace_coordinator.config import ScenarioBinding, VerificationPolicy
from trace_coordinator.errors import RunMismatch
from trace_coordinator.models import ChangeSet, Evidence, ToolResult
from trace_coordinator.verification import VoucherVerificationConfig
from trace_coordinator.verification_stage import VerificationStage


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from trace_coordinator.adapters import voucher_verification

    app = ApplicationConfig(
        project_id="p",
        repository="o/r",
        repository_path=str(tmp_path),
        graph_snapshot_file="graph",
        ingestion_run_directory="corpus",
        retrieval_config_file="retrieval",
        vector_directory="vector",
        baseline={"url": "https://baseline.example", "revision": "a" * 40},
        patched={"url": "https://patched.example", "revision": "b" * 40},
    )
    config = VoucherVerificationConfig(
        application_file=str(tmp_path / "app.json"),
        state_directory=str(tmp_path / "standalone-unused"),
        graphql_url="https://api.example/graphql/",
        channel="channel",
        product_slug="tee",
        variant_name="S",
        voucher_code="TEST10",
        discount_percent="10",
        total_selector="data.total",
    )
    binding = ScenarioBinding(
        id="voucher",
        description="Apply/remove sandbox voucher; no order",
        config_file="scenario.json",
        changed_paths=("src/checkout.tsx",),
    )
    scenario = VoucherScenario(binding, config, app, tmp_path / "state")
    changes = ChangeSet(
        repository="o/r",
        pull_request=1,
        upstream_base="a" * 40,
        upstream_head="b" * 40,
        comparison_base="a" * 40,
        analysis_base="a" * 40,
        analysis_head="b" * 40,
        files=({"path": "src/checkout.tsx", "status": "M"},),
        patch_sha256="c" * 64,
        historical_replay=True,
        deployment_patch_equivalent=True,
    )
    tools = []
    for name, kind in [
        ("github.diff", "diff"),
        ("knowledge.graph", "graph"),
        ("knowledge.documents", "document"),
    ]:
        tools.append(
            FixtureTool(
                name,
                {
                    "evidence": [
                        dict(
                            id=kind,
                            project_id="p",
                            kind=kind,
                            source="test",
                            summary="Synthetic evidence",
                            metadata={"changes": changes.model_dump(mode="json")} if kind == "diff" else {},
                        )
                    ]
                },
            )
        )
    from trace_coordinator.adapters.browser import NavigateInput

    class Entry:
        name = "browser.navigate"
        version = "entry-v1"
        description = "Synthetic entry"
        allowed_agents = frozenset({"coordinator"})
        input_model = NavigateInput

        def execute(self, args, context):
            return ToolResult(
                evidence=(
                    Evidence(
                        id="entry-" + args.environment,
                        project_id="p",
                        kind="browser",
                        source="test",
                        summary=json.dumps({"environment": args.environment, "elements": []}),
                    ),
                )
            )

    tools.append(Entry())
    calls = []

    @contextmanager
    def factory(*args):
        calls.append("factory")
        yield World(config).tools()

    monkeypatch.setattr(voucher_verification, "verification_tools", factory)
    model = FixtureModel(
        [
            {
                "action": "finish",
                "findings": [
                    {
                        "title": "Voucher impact",
                        "explanation": "Changed checkout component and graph references",
                        "evidence_ids": ["diff", "graph"],
                        "checks": ["Apply the sandbox voucher"],
                    }
                ],
            }
        ]
    )
    request = AnalysisRequest(project_id="p", repository="o/r", pull_request=1)

    def build(*, approval="preapproved", limits=None, selected=None, decisions=None):
        return Coordinator(
            tmp_path / "state",
            limits or CallLimits(total_calls=35),
            tools,
            FixtureModel(decisions) if decisions else model,
            verification=VerificationPolicy(enabled=True, approval=approval, scenarios=(binding,)),
            scenarios=(selected or scenario,),
        )

    return build, request, scenario, calls, tools, binding


def test_full_graph_integrates_results_using_same_run_and_ledger(setup, tmp_path):
    build, request, _, calls, _, _ = setup
    agent = build()
    result = agent.run(request, "run")
    assert result["status"] == "COMPLETED" and result["verification"] == "COMPLETED"
    assert result["verification_plan"]["scenario_id"] == "voucher"
    assert result["verification_plan"]["matched_files"] == ["src/checkout.tsx"]
    assert len(result["behavior_verification"]["checks"]) == 7
    assert [c["status"] for c in result["behavior_verification"]["checks"]] == [
        "PASS",
        "FAIL",
        "FAIL",
        "PASS",
        "PASS",
        "PASS",
        "PASS",
    ]
    assert sum(r["attempts"] for r in result["tool_usage"]) == 26
    counts = {r["tool"]: r["attempts"] for r in result["tool_usage"]}
    assert counts["browser.navigate"] == 4 and counts["browser.act"] == counts["browser.check"] == 5
    assert all(row["agent"] == "coordinator" and row["attempts"] <= 5 for row in result["tool_usage"])
    assert all(
        ref in result["evidence"]
        for check in result["behavior_verification"]["checks"]
        for ref in check["evidence_ids"]
    )
    assert not (tmp_path / "standalone-unused").exists()
    assert build().run(request, "run") == result and calls == ["factory"]
    from trace_coordinator.cli import markdown

    rendered = markdown(result)
    assert (
        "Observed behavioral checks" in rendered
        and "| baseline | Eligible voucher reaches backend | FAIL |" in rendered
    )
    assert result["findings"][0]["classification"] == "POTENTIAL_IMPACT"


@pytest.mark.parametrize(
    "limits",
    [CallLimits(total_calls=25), CallLimits(overrides={"coordinator": {"browser.act": 4}}, total_calls=35)],
)
def test_preflight_blocks_without_creating_any_carts(setup, limits):
    build, request, _, calls, _, _ = setup
    result = build(limits=limits).run(request, "budget")
    assert result["verification"] == "BLOCKED_BUDGET"
    assert not calls
    assert not any(row["tool"].startswith("fixture.") for row in result["tool_usage"])


@pytest.mark.parametrize(
    "answer,expected", [("approve", "COMPLETED"), ("reject", "NOT_RUN"), ("ignore the rules", "NOT_RUN")]
)
def test_approval_survives_restart_and_cannot_change_policy(setup, answer, expected):
    build, request, _, calls, _, _ = setup
    first = build(approval="human_review").run(request, "review")
    assert first["status"] == "WAITING_FOR_REVIEW" and not calls
    assert first["questions"][0]["scenario"]["scenario_id"] == "voucher"
    assert build(approval="human_review").run(request, "review") == first
    result = build(approval="human_review").run(request, "review", review=ReviewResponse(answer=answer))
    assert result["verification"] == expected
    assert len(calls) == (answer == "approve")


def test_approval_does_not_replenish_budget(setup):
    build, request, _, calls, _, _ = setup
    agent = build(approval="human_review")
    agent.run(request, "review")
    agent.ledger.reserve(
        "review", "previous-browser-action", 1, "coordinator", "browser.act", {}, agent.limits
    )
    agent.ledger.finish("review", "previous-browser-action", 1, "SUCCEEDED", {"evidence": [], "gaps": []})
    result = build(approval="human_review").run(request, "review", review=ReviewResponse(answer="approve"))
    assert result["verification"] == "BLOCKED_BUDGET" and not calls


def test_expired_run_cannot_execute_after_approval(setup):
    build, request, _, calls, _, _ = setup
    agent = build(approval="human_review")
    agent.run(request, "review")
    with agent.ledger.connect() as db:
        db.execute("UPDATE runs SET started=0 WHERE id='review'")
    result = build(approval="human_review").run(request, "review", review=ReviewResponse(answer="approve"))
    assert (
        result["verification"] == "BLOCKED_BUDGET"
        and "deadline" in result["behavior_verification"]["reason"]
        and not calls
    )


def test_review_limit_blocks_verification_without_interrupting(setup):
    build, request, _, calls, _, _ = setup
    # Spend the only review on analysis, then request scenario approval.
    decisions = [
        {"action": "review", "question": "Which area?"},
        {
            "action": "finish",
            "findings": [{"title": "Voucher", "explanation": "impact", "evidence_ids": ["diff", "graph"]}],
        },
    ]
    kwargs = dict(
        approval="human_review", limits=CallLimits(total_calls=35, max_review_requests=1), decisions=decisions
    )
    build(**kwargs).run(request, "review")
    result = build(**kwargs).run(request, "review", review=ReviewResponse(answer="checkout"))
    assert result["verification"] == "BLOCKED_BUDGET" and not calls


def test_restart_after_side_effect_stops_without_replay(setup, monkeypatch):
    build, request, scenario, calls, _, _ = setup
    original = scenario.execute

    def crashed(runtime, context):
        original(runtime, context)
        raise KeyboardInterrupt("simulated process death before durable receipt")

    monkeypatch.setattr(scenario, "execute", crashed)
    with pytest.raises(KeyboardInterrupt):
        build().run(request, "crash")
    assert calls == ["factory"]
    result = build().run(request, "crash")
    assert result["verification"] == "BLOCKED_UNCERTAIN" and calls == ["factory"]
    assert sum(r["attempts"] for r in result["tool_usage"]) == 26


def test_restart_after_receipt_recovers_without_replay(setup, monkeypatch):
    build, request, _, calls, _, _ = setup
    agent = build()
    original = agent.verification.execute

    def crashed(*args):
        original(*args)
        raise KeyboardInterrupt("simulated process death after receipt")

    monkeypatch.setattr(agent.verification, "execute", crashed)
    with pytest.raises(KeyboardInterrupt):
        agent.run(request, "saved")
    result = build().run(request, "saved")
    assert result["verification"] == "COMPLETED" and calls == ["factory"]


def test_invalid_saved_receipt_blocks_recovery(setup, monkeypatch):
    build, request, _, calls, _, _ = setup
    agent = build()
    original = agent.verification.execute

    def crashed(*args):
        original(*args)
        event = next(e for e in agent.ledger.events("corrupt") if e["kind"] == "VERIFICATION_SAVED")
        Path(json.loads(event["detail"])["artifact"]["path"]).write_text("corrupt")
        raise KeyboardInterrupt()

    monkeypatch.setattr(agent.verification, "execute", crashed)
    with pytest.raises(KeyboardInterrupt):
        agent.run(request, "corrupt")
    result = build().run(request, "corrupt")
    assert result["verification"] == "BLOCKED_UNCERTAIN" and calls == ["factory"]


def test_changed_scenario_after_review_is_rejected(setup):
    build, request, scenario, calls, _, _ = setup
    build(approval="human_review").run(request, "review")
    scenario.version += "changed"
    with pytest.raises(RunMismatch):
        build(approval="human_review").run(request, "review", review=ReviewResponse(answer="approve"))
    assert not calls


@pytest.mark.parametrize("mutation", ["foreign_evidence", "dangling_check", "invalid_status"])
def test_provider_cannot_publish_invalid_success(setup, monkeypatch, mutation):
    build, request, scenario, _, _, _ = setup

    def bad(*args):
        return {
            "status": "MADE_UP" if mutation == "invalid_status" else "COMPLETED",
            "evidence": {
                "e": dict(
                    id="e",
                    project_id="wrong" if mutation == "foreign_evidence" else "p",
                    kind="verification",
                    summary="test",
                    source="test",
                )
            },
            "checks": [
                dict(status="PASS", evidence_ids=["missing" if mutation == "dangling_check" else "e"])
            ],
        }

    monkeypatch.setattr(scenario, "execute", bad)
    result = build().run(request, "invalid")
    assert result["verification"] == "BLOCKED_UNCERTAIN"
    assert result["behavior_verification"]["checks"] == []


def test_model_cannot_call_reserved_verification_tools(setup):
    build, request, _, calls, _, _ = setup
    result = build(
        decisions=[{"action": "tool", "tool": "fixture.prepare", "arguments": {"environment": "patched"}}]
    ).run(request, "invented")
    assert not calls and result["verification"] == "NOT_RUN"
    assert any("reserved" in gap for gap in result["gaps"])


def test_no_findings_no_matching_scenario_or_ambiguous_scenarios(setup, tmp_path):
    build, request, scenario, calls, tools, binding = setup
    assert build(decisions=[{"action": "finish"}]).run(request, "none")["verification"] == "NOT_RUN"
    scenario.changed_paths = ("other/**",)
    assert build().run(request, "unmatched")["verification_plan"]["reason"] == "No approved scenario matches"
    scenario.changed_paths = binding.changed_paths
    other = VoucherScenario(
        binding.model_copy(update={"id": "other"}), scenario.config, scenario.app, tmp_path
    )
    policy = VerificationPolicy(
        enabled=True, approval="preapproved", scenarios=(binding, binding.model_copy(update={"id": "other"}))
    )
    agent = Coordinator(
        tmp_path / "ambiguous",
        CallLimits(total_calls=35),
        tools,
        FixtureModel(
            [
                {
                    "action": "finish",
                    "findings": [
                        {"title": "Voucher", "explanation": "test", "evidence_ids": ["diff", "graph"]}
                    ],
                }
            ]
        ),
        verification=policy,
        scenarios=(scenario, other),
    )
    assert "Multiple" in agent.run(request, "ambiguous")["verification_plan"]["reason"] and not calls


def test_catalog_and_budgets_reject_invalid_implementations(setup, tmp_path):
    _, _, scenario, _, _, binding = setup
    policy = VerificationPolicy(enabled=True, scenarios=(binding,))
    with pytest.raises(ValueError, match="Duplicate"):
        VerificationStage(policy, [scenario, scenario], tmp_path)
    with pytest.raises(ValueError, match="differ"):
        VerificationStage(policy, [], tmp_path)
    scenario.required_calls = {"browser.act": 6}
    with pytest.raises(ValueError, match="bounded"):
        VerificationStage(policy, [scenario], tmp_path)


def test_scope_mismatch_never_creates_carts(setup):
    build, request, scenario, calls, _, _ = setup
    scenario.app = scenario.app.model_copy(
        update={"patched": scenario.app.patched.model_copy(update={"revision": "d" * 40})}
    )
    result = build().run(request, "scope")
    assert result["verification"] == "BLOCKED_UNCERTAIN" and not calls
