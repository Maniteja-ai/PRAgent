"""Stable response-formatting boundary used by CLI and webhook delivery."""

from typing import Protocol

from trace_coordinator.domain.contracts import AnalysisReportPayload


class ResponseFormatter(Protocol):
    def format(self, report: AnalysisReportPayload) -> str: ...
