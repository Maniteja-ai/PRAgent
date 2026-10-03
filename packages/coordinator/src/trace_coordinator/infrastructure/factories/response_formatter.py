"""Build the configured response formatter and own provider resources."""

from contextlib import ExitStack

from trace_coordinator.config import (
    FixtureProvider,
    GeminiProvider,
    OpenAIProvider,
    ResponseFormatterConfig,
)
from trace_coordinator.response_formatter import (
    FallbackResponseFormatter,
    LlmResponseFormatter,
    ResponseFormatter,
    TemplateResponseFormatter,
)


class ResponseFormatterFactory:
    def __init__(self, resources: ExitStack) -> None:
        self.resources = resources

    def create(
        self,
        config: ResponseFormatterConfig,
        model: FixtureProvider | GeminiProvider | OpenAIProvider,
    ) -> ResponseFormatter:
        template = TemplateResponseFormatter()
        if config.provider == "template":
            return template
        if isinstance(model, FixtureProvider):
            raise ValueError("LLM response formatting requires a live model provider")
        llm = LlmResponseFormatter(model)
        self.resources.callback(llm.close)
        return FallbackResponseFormatter(llm, template)
