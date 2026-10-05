"""Typed application dependencies, supplied to services through constructors."""

from dataclasses import dataclass

from impact_agent.config.settings import AgentSettings
from impact_agent.evaluation.stages.interface.stage_recorder import StageRecorder
from impact_agent.guardrails.interface.guardrail import Guardrail
from impact_agent.model.interface.decision_model import DecisionModel
from impact_agent.pipeline.interface.agent_pipeline import AgentPipeline
from impact_agent.pull_request.interface.pull_request_provider import PullRequestProvider
from impact_agent.report.interface.report_formatter import ReportFormatter
from impact_agent.run_history.interface.run_store import RunStore
from impact_agent.tools.behavior.interface.behavior_verifier import BehaviorVerifier
from impact_agent.tools.browser.interface.browser_explorer import BrowserExplorer
from impact_agent.tools.knowledge.interface.knowledge_retriever import KnowledgeRetriever
from impact_agent.webhook.implementations.job_worker import WebhookJobWorker
from impact_agent.webhook.interface.webhook_job_handler import WebhookJobQueue
from impact_agent.webhook.interface.webhook_receiver import WebhookReceiver


@dataclass(frozen=True, slots=True)
class AgentBeans:
    """All runtime dependencies for one agent instance, supplied at startup."""

    settings: AgentSettings
    pipeline: AgentPipeline
    worker: WebhookJobWorker
    webhook_receiver: WebhookReceiver
    webhook_jobs: WebhookJobQueue
    pull_requests: PullRequestProvider
    knowledge: KnowledgeRetriever
    decision_model: DecisionModel
    guardrails: Guardrail
    report_formatter: ReportFormatter
    run_store: RunStore
    stage_recorder: StageRecorder
    browser: BrowserExplorer | None = None
    behavior_verifier: BehaviorVerifier | None = None
