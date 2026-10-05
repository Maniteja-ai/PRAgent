"""Read the separate JSON files and validate each into a typed settings model."""

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from impact_agent.config.settings import AgentSettings
from impact_agent.config.validation.agent import AgentConfig
from impact_agent.config.validation.behavior import BehaviorConfig
from impact_agent.config.validation.browser import BrowserConfig
from impact_agent.config.validation.evaluation import EvaluationConfig
from impact_agent.config.validation.github import GitHubConfig
from impact_agent.config.validation.graph_database import GraphDatabaseConfig
from impact_agent.config.validation.guardrails import GuardrailConfig
from impact_agent.config.validation.knowledge import KnowledgeConfig
from impact_agent.config.validation.model import ModelConfig
from impact_agent.config.validation.run_history import RunHistoryConfig
from impact_agent.config.validation.runtime import RuntimeConfig
from impact_agent.config.validation.webhook import WebhookConfig

SettingsPart = TypeVar("SettingsPart", bound=BaseModel)


class ConfigurationError(ValueError):
    """Configuration file is missing, malformed, or does not match its model."""


class JsonConfigLoader:
    def load(self, directory: Path) -> AgentSettings:
        config_directory = directory.resolve()
        return AgentSettings(
            agent=self._load_file(config_directory, "agent.json", AgentConfig),
            github=self._load_file(config_directory, "github.json", GitHubConfig),
            graph_database=self._load_file(
                config_directory, "graph_database.json", GraphDatabaseConfig
            ),
            webhook=self._load_file(config_directory, "webhook.json", WebhookConfig),
            models=self._load_file(config_directory, "models.json", ModelConfig),
            knowledge=self._load_file(config_directory, "knowledge.json", KnowledgeConfig),
            browser=self._load_file(config_directory, "browser.json", BrowserConfig),
            behavior=self._load_file(config_directory, "behavior.json", BehaviorConfig),
            runtime=self._load_file(config_directory, "runtime.json", RuntimeConfig),
            run_history=self._load_file(config_directory, "run_history.json", RunHistoryConfig),
            guardrails=self._load_file(config_directory, "guardrails.json", GuardrailConfig),
            evaluation=self._load_file(config_directory, "evaluation.json", EvaluationConfig),
        )

    @staticmethod
    def _load_file(directory: Path, filename: str, model: type[SettingsPart]) -> SettingsPart:
        path = directory / filename
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ConfigurationError(f"{filename} must contain a JSON object")
            return model.model_validate(raw)
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Required configuration file is missing: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON in {path}: {exc.msg}") from exc
        except ValidationError as exc:
            raise ConfigurationError(f"Invalid settings in {path}: {exc}") from exc
