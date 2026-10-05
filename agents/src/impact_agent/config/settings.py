"""Combined application settings produced by the modular config loader."""

from impact_agent.config.validation.agent import AgentConfig
from impact_agent.config.validation.behavior import BehaviorConfig
from impact_agent.config.validation.browser import BrowserConfig
from impact_agent.config.validation.common import StrictSettings
from impact_agent.config.validation.evaluation import EvaluationConfig
from impact_agent.config.validation.github import GitHubConfig
from impact_agent.config.validation.graph_database import GraphDatabaseConfig
from impact_agent.config.validation.guardrails import GuardrailConfig
from impact_agent.config.validation.knowledge import KnowledgeConfig
from impact_agent.config.validation.model import ModelConfig
from impact_agent.config.validation.run_history import RunHistoryConfig
from impact_agent.config.validation.runtime import RuntimeConfig
from impact_agent.config.validation.webhook import WebhookConfig


class AgentSettings(StrictSettings):
    agent: AgentConfig
    github: GitHubConfig
    graph_database: GraphDatabaseConfig
    webhook: WebhookConfig
    models: ModelConfig
    knowledge: KnowledgeConfig
    browser: BrowserConfig
    behavior: BehaviorConfig
    runtime: RuntimeConfig
    run_history: RunHistoryConfig
    guardrails: GuardrailConfig
    evaluation: EvaluationConfig
