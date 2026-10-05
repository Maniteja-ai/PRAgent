from pathlib import Path

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import (
    AgentReport,
    BehaviorResult,
    BehaviorVerification,
    Decision,
    Evidence,
    Finding,
    PullRequestRef,
    PullRequestSnapshot,
)
from impact_agent.domain.pipeline_enums import CoverageGap
from impact_agent.pipeline.implementations.langgraph_agent_pipeline import (
    LangGraphAgentPipeline,
)

CONFIG = Path(__file__).parents[1] / "config" / "default"
REFERENCE = PullRequestRef("owner/storefront", 7)
PULL_REQUEST = PullRequestSnapshot(
    reference=REFERENCE,
    title="Update voucher behavior",
    description="Keep applied discount in sync with cart changes.",
    base_sha="base-sha",
    head_sha="head-sha",
    files=(),
    diff="diff --git a/cart.py b/cart.py",
)
CODE_EVIDENCE = Evidence("code-1", "cart.py", "voucher application", "sha256-code")
UI_EVIDENCE = Evidence("ui-1", "browser", "cart page", "sha256-ui")
FINDING = Finding("Cart total may be stale", "Update totals after voucher changes.", ("code-1",))
DECISION = Decision("The cart total flow may be affected.", (FINDING,))


class FakeAdapters:
    def __init__(self) -> None:
        self.saved: list[AgentReport] = []
        self.recorded: list[tuple[str, str]] = []
        self.guardrail_calls: list[str] = []
        self.browser_calls = 0
        self.behavior_calls = 0
        self.decision_evidence = ()

    def fetch(self, reference: PullRequestRef) -> PullRequestSnapshot:
        assert reference == REFERENCE
        return PULL_REQUEST

    def retrieve(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        assert pull_request == PULL_REQUEST
        return (CODE_EVIDENCE,)

    def decide(self, pull_request, evidence) -> Decision:
        assert pull_request == PULL_REQUEST
        assert CODE_EVIDENCE in evidence
        self.decision_evidence = evidence
        return DECISION

    def validate_request(self, reference: PullRequestRef) -> None:
        assert reference == REFERENCE
        self.guardrail_calls.append("request")

    def validate_evidence(self, evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        self.guardrail_calls.append("evidence")
        return evidence

    def validate_decision(self, decision: Decision, evidence: tuple[Evidence, ...]) -> Decision:
        assert decision == DECISION
        self.guardrail_calls.append("decision")
        return decision

    def validate_output(self, report_text: str) -> str:
        self.guardrail_calls.append("output")
        return report_text

    def format(self, report: AgentReport) -> str:
        return f"# Impact report\n\n{report.summary}"

    def save(self, report: AgentReport) -> None:
        self.saved.append(report)

    def load(self, run_id: str) -> AgentReport | None:
        return next((report for report in self.saved if report.run_id == run_id), None)

    def record(self, run_id: str, result) -> None:
        self.recorded.append((run_id, result.stage))

    def explore(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        self.browser_calls += 1
        return (UI_EVIDENCE,)

    def verify(self, pull_request, findings, evidence) -> BehaviorVerification:
        self.behavior_calls += 1
        return BehaviorVerification(
            results=(BehaviorResult("voucher-change", "PASS", "Verified cart total update."),)
        )


def create_pipeline(settings, adapters: FakeAdapters) -> LangGraphAgentPipeline:
    return LangGraphAgentPipeline(
        settings=settings,
        pull_requests=adapters,
        knowledge=adapters,
        decision_model=adapters,
        guardrails=adapters,
        report_formatter=adapters,
        run_store=adapters,
        stage_recorder=adapters,
        browser=adapters if settings.agent.browser_enabled else None,
        behavior_verifier=adapters if settings.agent.behavior_checks_enabled else None,
    )


def settings_with_live_adapters_disabled():
    settings = JsonConfigLoader().load(CONFIG)
    return settings.model_copy(
        update={
            "agent": settings.agent.model_copy(
                update={"browser_enabled": False, "behavior_checks_enabled": False}
            )
        }
    )


def test_graph_runs_stages_guards_and_persists_honest_gaps():
    settings = settings_with_live_adapters_disabled()
    adapters = FakeAdapters()
    pipeline = create_pipeline(settings, adapters)

    report = pipeline.run(REFERENCE, "run-1")

    assert report.status == "COMPLETED_WITH_GAPS"
    assert report.gaps == ("UI exploration is disabled", "Behavior verification is disabled")
    assert report.findings == (FINDING,)
    assert report.evidence == (CODE_EVIDENCE,)
    assert report.rendered_report.startswith("# Impact report")
    assert adapters.saved == [report]
    assert adapters.guardrail_calls == ["request", "evidence", "decision", "output"]
    assert adapters.recorded == [
        ("run-1", "Validate PR request"),
        ("run-1", "Fetch pull request"),
        ("run-1", "Retrieve knowledge"),
        ("run-1", "Explore UI"),
        ("run-1", "Analyze impact"),
        ("run-1", "Verify behavior"),
        ("run-1", "Create report"),
    ]
    assert adapters.browser_calls == adapters.behavior_calls == 0


def test_enabled_ui_and_behavior_stages_run_and_add_evidence():
    settings = settings_with_live_adapters_disabled()
    configured = settings.model_copy(
        update={
            "agent": settings.agent.model_copy(
                update={"browser_enabled": True, "behavior_checks_enabled": True}
            )
        }
    )
    adapters = FakeAdapters()
    pipeline = create_pipeline(configured, adapters)

    report = pipeline.run(REFERENCE, "run-2")

    assert report.status == "COMPLETED"
    assert report.gaps == ()
    assert report.evidence == (CODE_EVIDENCE, UI_EVIDENCE)
    assert report.behavior_results[0].status == "PASS"
    assert adapters.guardrail_calls.count("evidence") == 2
    assert adapters.browser_calls == adapters.behavior_calls == 1


def test_pipeline_marks_uncovered_confirmed_ui_route_as_pr_specific_gap():
    settings = settings_with_live_adapters_disabled()
    configured = settings.model_copy(
        update={
            "agent": settings.agent.model_copy(
                update={"browser_enabled": True, "behavior_checks_enabled": True}
            )
        }
    )

    class UncoveredBehaviorAdapters(FakeAdapters):
        def verify(self, pull_request, findings, evidence) -> BehaviorVerification:
            self.behavior_calls += 1
            return BehaviorVerification(
                coverage_gaps=("No configured behavior scenario covers /checkout",)
            )

    adapters = UncoveredBehaviorAdapters()
    report = create_pipeline(configured, adapters).run(REFERENCE, "run-behavior-gap")

    assert report.status == "COMPLETED_WITH_GAPS"
    assert report.gaps == ("No configured behavior scenario covers /checkout",)
    assert report.behavior_results == ()


def test_factory_rejects_enabled_ui_stage_without_adapter():
    settings = settings_with_live_adapters_disabled()
    configured = settings.model_copy(
        update={"agent": settings.agent.model_copy(update={"browser_enabled": True})}
    )

    try:
        LangGraphAgentPipeline(
            settings=configured,
            pull_requests=FakeAdapters(),
            knowledge=FakeAdapters(),
            decision_model=FakeAdapters(),
            guardrails=FakeAdapters(),
            report_formatter=FakeAdapters(),
            run_store=FakeAdapters(),
            stage_recorder=FakeAdapters(),
        )
    except ValueError as error:
        assert "BrowserExplorer" in str(error)
    else:
        raise AssertionError("Enabled browser stage must have an implementation")


def test_graph_coverage_markers_are_reported_but_not_sent_as_factual_evidence():
    settings = settings_with_live_adapters_disabled()

    class CoverageGapAdapters(FakeAdapters):
        def retrieve(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
            return (
                CODE_EVIDENCE,
                Evidence(
                    "coverage-gap-1",
                    "neo4j-coverage://changed-files",
                    "No matching indexed code graph node was found.",
                    "sha256-gap",
                    CoverageGap.CODE_GRAPH_DATA_MISSING,
                ),
            )

    adapters = CoverageGapAdapters()
    report = create_pipeline(settings, adapters).run(REFERENCE, "run-gap")

    assert report.status == "COMPLETED_WITH_GAPS"
    assert CoverageGap.CODE_GRAPH_DATA_MISSING.value in report.gaps
    assert report.evidence[1].coverage_gap is CoverageGap.CODE_GRAPH_DATA_MISSING
    assert adapters.decision_evidence == (CODE_EVIDENCE,)
