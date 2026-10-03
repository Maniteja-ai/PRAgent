"""Create the optional report writer selected by coordinator configuration."""

from contextlib import ExitStack

from trace_coordinator.application.interfaces import ReportWriter
from trace_coordinator.config import FixtureProvider, GeminiProvider, OpenAIProvider, ReportConfig


class ReportWriterFactory:
    def __init__(self, resources: ExitStack) -> None:
        self.resources = resources

    def create(
        self,
        report: ReportConfig,
        model: FixtureProvider | GeminiProvider | OpenAIProvider,
    ) -> ReportWriter | None:
        if report.writer == "template":
            return None
        if isinstance(model, FixtureProvider):
            raise ValueError("LLM report writing requires a live model provider")
        from trace_coordinator.infrastructure.adapters.langchain_report_writer import (
            LangChainReportWriter,
        )

        writer = LangChainReportWriter(model)
        self.resources.callback(writer.close)
        return writer
