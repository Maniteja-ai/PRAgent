"""Deterministic response formatter used directly and as the safe fallback."""

from trace_coordinator.domain.contracts import AnalysisReportPayload
from trace_coordinator.response_formatter.dependencies import render_markdown
from trace_coordinator.response_formatter.interface import ResponseFormatter


class TemplateResponseFormatter(ResponseFormatter):
    def format(self, report: AnalysisReportPayload) -> str:
        return render_markdown(report)
