"""Decision-model factory with resource ownership kept outside the workflow."""

import json
from contextlib import ExitStack

from trace_coordinator.config import FixtureProvider, GeminiProvider, OpenAIProvider
from trace_coordinator.decision_model.implementations.fixture import FixtureDecisionModel
from trace_coordinator.decision_model.interface import DecisionModel
from trace_coordinator.domain.contracts import as_json_object
from trace_coordinator.infrastructure.dependencies import BootstrapContext


class DecisionModelFactory:
    def __init__(self, resources: ExitStack, context: BootstrapContext) -> None:
        self.resources = resources
        self.context = context

    def create(self, config: FixtureProvider | GeminiProvider | OpenAIProvider) -> DecisionModel:
        if config.provider == "fixture":
            return self._fixture(config)
        return self._live(config)

    def _fixture(self, config: FixtureProvider) -> DecisionModel:
        document = as_json_object(json.loads(self.context.resolve(config.file).read_text(encoding="utf-8")))
        decisions = document.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("Fixture model file requires a decisions list")
        return FixtureDecisionModel(decisions)

    def _live(self, config: GeminiProvider | OpenAIProvider) -> DecisionModel:
        from trace_coordinator.decision_model.implementations.langchain import LangChainDecisionModel

        model = LangChainDecisionModel(config)
        self.resources.callback(model.close)
        return model
