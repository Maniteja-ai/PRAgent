"""Deterministic response formatter used directly and as the safe fallback."""

from trace_coordinator.domain.contracts import AnalysisReportPayload
from trace_coordinator.presentation.response_formatter.interface import ResponseFormatter
from trace_coordinator.presentation.response_formatter.renderer import render_markdown


class TemplateResponseFormatter(ResponseFormatter):
    def format(self, report: AnalysisReportPayload) -> str:
        return render_markdown(report)
