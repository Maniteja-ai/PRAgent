"""One readable LangGraph coordinator for the complete PR impact-analysis flow."""

from dataclasses import dataclass, field, replace
from typing import cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from impact_agent.config.settings import AgentSettings
from impact_agent.domain.models import (
    AgentReport,
    Evidence,
    PullRequestRef,
    PullRequestSnapshot,
)
from impact_agent.domain.pipeline_enums import (
    CoverageGap,
    EvaluationStage,
    PipelineNode,
    ReportStatus,
)
from impact_agent.domain.state import AgentState
from impact_agent.evaluation.stages.implementations.recording_decorator import (
    EvaluationRecordingDecorator,
)
from impact_agent.evaluation.stages.interface.stage_recorder import StageRecorder
from impact_agent.guardrails.interface.guardrail import Guardrail
from impact_agent.model.interface.decision_model import DecisionModel
from impact_agent.pipeline.interface.agent_pipeline import AgentPipeline
from impact_agent.pipeline.interface.pipeline_stage import PipelineStage
from impact_agent.pull_request.interface.pull_request_provider import PullRequestProvider
from impact_agent.report.interface.report_formatter import ReportFormatter
from impact_agent.run_history.interface.run_store import RunStore
from impact_agent.tools.behavior.interface.behavior_verifier import BehaviorVerifier
from impact_agent.tools.browser.interface.browser_explorer import BrowserExplorer
from impact_agent.tools.knowledge.interface.knowledge_retriever import KnowledgeRetriever


@dataclass(slots=True)
class LangGraphAgentPipeline(AgentPipeline):
    """Coordinates each stage; provider implementations are injected behind interfaces."""

    settings: AgentSettings
    pull_requests: PullRequestProvider
    knowledge: KnowledgeRetriever
    decision_model: DecisionModel
    guardrails: Guardrail
    report_formatter: ReportFormatter
    run_store: RunStore
    stage_recorder: StageRecorder
    browser: BrowserExplorer | None = None
    behavior_verifier: BehaviorVerifier | None = None
    _graph: CompiledStateGraph[AgentState, None, AgentState, AgentState] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.settings.agent.browser_enabled and self.browser is None:
            raise ValueError("Browser exploration is enabled but no BrowserExplorer was supplied")
        if self.settings.agent.behavior_checks_enabled and self.behavior_verifier is None:
            raise ValueError("Behavior checks are enabled but no BehaviorVerifier was supplied")
        self._graph = self._build_graph()

    def run(self, reference: PullRequestRef, run_id: str) -> AgentReport:
        """Run one PR through the graph and return its persisted report."""
        result = cast(
            AgentState,
            self._graph.invoke(
                {"run_id": run_id, "request": reference, "gaps": (), "behavior_results": ()}
            ),
        )
        report = result.get("report")
        if report is None:
            raise RuntimeError("Analysis graph finished without creating a report")
        return report

    def _build_graph(self) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
        graph = StateGraph(AgentState)
        graph.add_node(
            PipelineNode.VALIDATE_PR_REQUEST,
            self._record(EvaluationStage.VALIDATE_PR_REQUEST, self._validate),
        )
        graph.add_node(
            PipelineNode.FETCH_PULL_REQUEST,
            self._record(EvaluationStage.FETCH_PULL_REQUEST, self._fetch),
        )
        graph.add_node(
            PipelineNode.RETRIEVE_KNOWLEDGE,
            self._record(EvaluationStage.RETRIEVE_KNOWLEDGE, self._retrieve),
        )
        graph.add_node(
            PipelineNode.EXPLORE_UI,
            self._record(EvaluationStage.EXPLORE_UI, self._explore_ui),
        )
        graph.add_node(
            PipelineNode.ANALYZE_IMPACT,
            self._record(EvaluationStage.ANALYZE_IMPACT, self._analyze),
        )
        graph.add_node(
            PipelineNode.VERIFY_BEHAVIOR,
            self._record(EvaluationStage.VERIFY_BEHAVIOR, self._verify_behavior),
        )
        graph.add_node(
            PipelineNode.CREATE_REPORT,
            self._record(EvaluationStage.CREATE_REPORT, self._create_report),
        )
        graph.add_edge(START, PipelineNode.VALIDATE_PR_REQUEST)
        graph.add_edge(PipelineNode.VALIDATE_PR_REQUEST, PipelineNode.FETCH_PULL_REQUEST)
        graph.add_edge(PipelineNode.FETCH_PULL_REQUEST, PipelineNode.RETRIEVE_KNOWLEDGE)
        graph.add_edge(PipelineNode.RETRIEVE_KNOWLEDGE, PipelineNode.EXPLORE_UI)
        graph.add_edge(PipelineNode.EXPLORE_UI, PipelineNode.ANALYZE_IMPACT)
        graph.add_edge(PipelineNode.ANALYZE_IMPACT, PipelineNode.VERIFY_BEHAVIOR)
        graph.add_edge(PipelineNode.VERIFY_BEHAVIOR, PipelineNode.CREATE_REPORT)
        graph.add_edge(PipelineNode.CREATE_REPORT, END)
        return cast(CompiledStateGraph[AgentState, None, AgentState, AgentState], graph.compile())

    def _record(self, name: EvaluationStage, stage: PipelineStage) -> PipelineStage:
        """Apply the evaluation decorator consistently to every graph node."""

        def recorded(state: AgentState) -> AgentState:
            run_id = state.get("run_id")
            if not run_id:
                raise ValueError("run_id is required in pipeline state")
            return EvaluationRecordingDecorator(
                stage_name=name.value,
                stage=stage,
                run_id=run_id,
                recorder=self.stage_recorder,
            )(state)

        return recorded

    def _validate(self, state: AgentState) -> AgentState:
        self.guardrails.validate_request(self._request(state))
        return {}

    def _fetch(self, state: AgentState) -> AgentState:
        return {"pull_request": self.pull_requests.fetch(self._request(state))}

    def _retrieve(self, state: AgentState) -> AgentState:
        gaps = state.get("gaps", ())
        if self.settings.knowledge.provider == "disabled":
            gaps = self._append_gap(state, CoverageGap.VECTOR_RETRIEVAL_DISABLED)
        evidence = self.knowledge.retrieve(self._pull_request(state))
        checked_evidence = self.guardrails.validate_evidence(evidence)
        for item in checked_evidence:
            if item.coverage_gap is not None and item.coverage_gap.value not in gaps:
                gaps += (item.coverage_gap.value,)
        return {"evidence": checked_evidence, "gaps": gaps}

    def _explore_ui(self, state: AgentState) -> AgentState:
        if not self.settings.agent.browser_enabled:
            return {"gaps": self._append_gap(state, CoverageGap.UI_EXPLORATION_DISABLED)}
        if self.browser is None:
            raise RuntimeError("UI exploration is enabled but BrowserExplorer is missing")
        captured = self.browser.explore(self._pull_request(state))
        evidence = self.guardrails.validate_evidence(self._evidence(state) + captured)
        return {"evidence": evidence}

    def _analyze(self, state: AgentState) -> AgentState:
        evidence = tuple(item for item in self._evidence(state) if item.coverage_gap is None)
        decision = self.decision_model.decide(self._pull_request(state), evidence)
        checked = self.guardrails.validate_decision(decision, evidence)
        return {"decision": checked}

    def _verify_behavior(self, state: AgentState) -> AgentState:
        if not self.settings.agent.behavior_checks_enabled:
            return {
                "gaps": self._append_gap(state, CoverageGap.BEHAVIOR_VERIFICATION_DISABLED),
                "behavior_results": (),
            }
        if self.behavior_verifier is None:
            raise RuntimeError("Behavior verification is enabled but BehaviorVerifier is missing")
        decision = state.get("decision")
        if decision is None:
            raise RuntimeError("Impact analysis did not produce a decision")
        verification = self.behavior_verifier.verify(
            self._pull_request(state), decision.findings, self._evidence(state)
        )
        gaps = state.get("gaps", ())
        gaps += tuple(gap for gap in verification.coverage_gaps if gap not in gaps)
        return {"behavior_results": verification.results, "gaps": gaps}

    def _create_report(self, state: AgentState) -> AgentState:
        decision = state.get("decision")
        if decision is None:
            raise RuntimeError("Impact analysis did not produce a decision")
        evidence = self._evidence(state)
        behavior_results = state.get("behavior_results", ())
        gaps = state.get("gaps", ())
        report = AgentReport(
            run_id=self._run_id(state),
            status=ReportStatus.COMPLETED_WITH_GAPS if gaps else ReportStatus.COMPLETED,
            summary=decision.summary,
            findings=decision.findings,
            evidence=evidence,
            behavior_results=behavior_results,
            gaps=gaps,
        )
        rendered = self.guardrails.validate_output(self.report_formatter.format(report))
        completed_report = replace(report, rendered_report=rendered)
        self.run_store.save(completed_report)
        return {"report": completed_report}

    @staticmethod
    def _request(state: AgentState) -> PullRequestRef:
        request = state.get("request")
        if request is None:
            raise RuntimeError("Pipeline state has no pull request reference")
        return request

    @staticmethod
    def _pull_request(state: AgentState) -> PullRequestSnapshot:
        pull_request = state.get("pull_request")
        if pull_request is None:
            raise RuntimeError("Pull request was not fetched")
        return pull_request

    @staticmethod
    def _evidence(state: AgentState) -> tuple[Evidence, ...]:
        return state.get("evidence", ())

    @staticmethod
    def _append_gap(state: AgentState, gap: CoverageGap) -> tuple[str, ...]:
        return state.get("gaps", ()) + (gap.value,)

    @staticmethod
    def _run_id(state: AgentState) -> str:
        run_id = state.get("run_id")
        if not run_id:
            raise ValueError("run_id is required in pipeline state")
        return run_id
