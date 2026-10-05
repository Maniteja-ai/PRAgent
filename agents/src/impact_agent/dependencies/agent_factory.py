"""Validate settings and assemble the typed dependencies for one agent instance."""

from impact_agent.config.settings import AgentSettings
from impact_agent.dependencies.agent_beans import AgentBeans
from impact_agent.evaluation.stages.interface.stage_recorder import StageRecorder
from impact_agent.guardrails.interface.guardrail import Guardrail
from impact_agent.model.interface.decision_model import DecisionModel
from impact_agent.pipeline.implementations.langgraph_agent_pipeline import (
    LangGraphAgentPipeline,
)
from impact_agent.pipeline.interface.agent_pipeline import AgentPipeline
from impact_agent.pull_request.interface.pull_request_provider import PullRequestProvider
from impact_agent.pull_request.interface.report_commenter import ReportCommenter
from impact_agent.report.interface.report_formatter import ReportFormatter
from impact_agent.run_history.interface.run_store import RunStore
from impact_agent.tools.behavior.interface.behavior_verifier import BehaviorVerifier
from impact_agent.tools.browser.interface.browser_explorer import BrowserExplorer
from impact_agent.tools.knowledge.interface.knowledge_retriever import KnowledgeRetriever
from impact_agent.webhook.implementations.job_worker import WebhookJobWorker
from impact_agent.webhook.interface.webhook_job_handler import WebhookJobQueue
from impact_agent.webhook.interface.webhook_receiver import WebhookReceiver


class AgentFactory:
    """Single place that wires chosen implementations to their interfaces."""

    @staticmethod
    def create(
        settings: AgentSettings,
        *,
        webhook_receiver: WebhookReceiver,
        webhook_jobs: WebhookJobQueue,
        pull_requests: PullRequestProvider,
        knowledge: KnowledgeRetriever,
        decision_model: DecisionModel,
        guardrails: Guardrail,
        report_formatter: ReportFormatter,
        run_store: RunStore,
        stage_recorder: StageRecorder,
        report_commenter: ReportCommenter | None = None,
        browser: BrowserExplorer | None = None,
        behavior_verifier: BehaviorVerifier | None = None,
    ) -> AgentBeans:
        if settings.agent.browser_enabled and browser is None:
            raise ValueError("Browser exploration is enabled but no BrowserExplorer was supplied")
        if settings.agent.behavior_checks_enabled and behavior_verifier is None:
            raise ValueError("Behavior checks are enabled but no BehaviorVerifier was supplied")
        pipeline: AgentPipeline = LangGraphAgentPipeline(
            settings=settings,
            pull_requests=pull_requests,
            knowledge=knowledge,
            decision_model=decision_model,
            guardrails=guardrails,
            report_formatter=report_formatter,
            run_store=run_store,
            stage_recorder=stage_recorder,
            browser=browser,
            behavior_verifier=behavior_verifier,
        )
        worker = WebhookJobWorker(
            webhook_jobs, pipeline, settings.runtime, report_commenter=report_commenter
        )
        return AgentBeans(
            settings=settings,
            pipeline=pipeline,
            worker=worker,
            webhook_receiver=webhook_receiver,
            webhook_jobs=webhook_jobs,
            pull_requests=pull_requests,
            knowledge=knowledge,
            decision_model=decision_model,
            guardrails=guardrails,
            report_formatter=report_formatter,
            run_store=run_store,
            stage_recorder=stage_recorder,
            browser=browser,
            behavior_verifier=behavior_verifier,
        )
