"""Load user settings, build configured adapters, and return the running agent services."""

import os
from collections.abc import Mapping
from pathlib import Path
from threading import Event
from typing import Protocol

from dotenv import load_dotenv
from fastapi import FastAPI

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.config.settings import AgentSettings
from impact_agent.dependencies.agent_beans import AgentBeans
from impact_agent.dependencies.agent_factory import AgentFactory
from impact_agent.evaluation.stages.implementations.disabled_stage_recorder import (
    DisabledStageRecorder,
)
from impact_agent.guardrails.implementations.basic_guardrail import BasicGuardrail
from impact_agent.model.implementations.gemini_decision_model import GeminiDecisionModelFactory
from impact_agent.pull_request.implementations.github_api_provider import (
    GitHubPullRequestProviderFactory,
)
from impact_agent.report.implementations.markdown_report_formatter import MarkdownReportFormatter
from impact_agent.run_history.implementations.sqlite_run_history_store import SQLiteRunHistoryStore
from impact_agent.tools.behavior.interface.behavior_verifier import BehaviorVerifier
from impact_agent.tools.browser.implementations.playwright_adapters import (
    PlaywrightAdapterFactory,
)
from impact_agent.tools.browser.interface.browser_explorer import BrowserExplorer
from impact_agent.tools.knowledge.implementations.composite_knowledge_retriever import (
    CompositeKnowledgeRetriever,
)
from impact_agent.tools.knowledge.implementations.disabled_knowledge_retriever import (
    DisabledKnowledgeRetriever,
)
from impact_agent.tools.knowledge.implementations.neo4j_code_graph_retriever import (
    Neo4jCodeGraphRetrieverFactory,
)
from impact_agent.tools.knowledge.implementations.qdrant_knowledge_retriever import (
    QdrantKnowledgeRetrieverFactory,
)
from impact_agent.tools.knowledge.interface.knowledge_retriever import KnowledgeRetriever
from impact_agent.webhook.implementations.fastapi_app import create_webhook_app
from impact_agent.webhook.implementations.receiver_factory import GitHubWebhookReceiverFactory
from impact_agent.webhook.implementations.sqlite_job_queue import SQLiteWebhookJobQueue


class _Closeable(Protocol):
    def close(self) -> None: ...


class AgentApplication:
    """Ready-to-run API and worker dependencies, with explicit resource cleanup."""

    def __init__(
        self,
        settings: AgentSettings,
        beans: AgentBeans,
        webhook_application: FastAPI,
        resources: tuple[_Closeable, ...],
    ) -> None:
        self.settings = settings
        self.beans = beans
        self.webhook_application = webhook_application
        self._resources = resources

    def run_worker(self, stop_event: Event) -> None:
        self.beans.worker.run_forever(stop_event)

    def close(self) -> None:
        for resource in reversed(self._resources):
            resource.close()

    def __enter__(self) -> "AgentApplication":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AgentBootstrap:
    """The one startup entry point for configured webhook, queue, agent, and worker."""

    @staticmethod
    def create(
        config_directory: Path,
        project_directory: Path,
        *,
        environment: Mapping[str, str],
        browser: BrowserExplorer | None = None,
        behavior_verifier: BehaviorVerifier | None = None,
    ) -> AgentApplication:
        settings = JsonConfigLoader().load(config_directory)
        root = project_directory.resolve()
        runtime_environment = AgentBootstrap._load_environment(
            root, settings.agent.env_file, environment
        )
        resources: list[_Closeable] = []
        try:
            configured_browser, configured_verifier = PlaywrightAdapterFactory.create(
                settings.browser,
                settings.behavior,
                browser_enabled=settings.agent.browser_enabled and browser is None,
                behavior_enabled=(
                    settings.agent.behavior_checks_enabled and behavior_verifier is None
                ),
            )
            browser = browser or configured_browser
            behavior_verifier = behavior_verifier or configured_verifier
            pull_requests = GitHubPullRequestProviderFactory.create(
                settings.github, runtime_environment
            )
            resources.append(pull_requests)
            decision_model = GeminiDecisionModelFactory.create(
                settings.models, settings.runtime.request_timeout_seconds, runtime_environment
            )
            resources.append(decision_model)
            knowledge: KnowledgeRetriever
            retrievers: list[KnowledgeRetriever] = []
            if settings.knowledge.provider == "qdrant":
                vector_retriever = QdrantKnowledgeRetrieverFactory.create(
                    settings.knowledge,
                    settings.models,
                    settings.runtime.request_timeout_seconds,
                    runtime_environment,
                    project_directory=root,
                )
                resources.append(vector_retriever)
                retrievers.append(vector_retriever)
            else:
                retrievers.append(DisabledKnowledgeRetriever())
            graph_retriever = Neo4jCodeGraphRetrieverFactory.create(
                settings.graph_database,
                settings.knowledge.max_evidence,
                runtime_environment,
                settings.models,
                settings.runtime.request_timeout_seconds,
            )
            if graph_retriever is not None:
                resources.append(graph_retriever)
                retrievers.append(graph_retriever)
            knowledge = CompositeKnowledgeRetriever(
                tuple(retrievers), settings.knowledge.max_evidence
            )

            history = SQLiteRunHistoryStore(
                AgentBootstrap._resolve_path(root, settings.run_history.database_path)
            )
            webhook_queue = SQLiteWebhookJobQueue(
                AgentBootstrap._resolve_path(root, settings.webhook.database_path)
            )
            stage_recorder = (
                history if settings.evaluation.record_stage_results else DisabledStageRecorder()
            )
            beans = AgentFactory.create(
                settings,
                webhook_receiver=GitHubWebhookReceiverFactory.create(
                    settings.webhook, runtime_environment
                ),
                webhook_jobs=webhook_queue,
                pull_requests=pull_requests,
                knowledge=knowledge,
                decision_model=decision_model,
                guardrails=BasicGuardrail(settings.guardrails),
                report_formatter=MarkdownReportFormatter(),
                run_store=history,
                stage_recorder=stage_recorder,
                browser=browser,
                behavior_verifier=behavior_verifier,
            )
            webhook_application = create_webhook_app(
                settings.webhook, beans.webhook_receiver, beans.webhook_jobs
            )
            return AgentApplication(settings, beans, webhook_application, tuple(resources))
        except Exception:
            for resource in reversed(resources):
                resource.close()
            raise

    @staticmethod
    def _resolve_path(project_directory: Path, configured_path: Path) -> Path:
        if configured_path.is_absolute():
            return configured_path
        return project_directory / configured_path

    @staticmethod
    def _load_environment(
        project_directory: Path,
        environment_file: Path | None,
        supplied_environment: Mapping[str, str],
    ) -> Mapping[str, str]:
        if environment_file is not None:
            load_dotenv(
                AgentBootstrap._resolve_path(project_directory, environment_file),
                override=False,
            )
        merged_environment = dict(os.environ)
        merged_environment.update(supplied_environment)
        return merged_environment
