"""Try a primary response formatter and safely fall back to another implementation."""

import logging

from trace_coordinator.domain.contracts import AnalysisReportPayload
from trace_coordinator.presentation.response_formatter.interface import ResponseFormatter

LOGGER = logging.getLogger(__name__)


class FallbackResponseFormatter(ResponseFormatter):
    def __init__(self, primary: ResponseFormatter, fallback: ResponseFormatter) -> None:
        self.primary = primary
        self.fallback = fallback

    def format(self, report: AnalysisReportPayload) -> str:
        try:
            return self.primary.format(report)
        except Exception as exc:
            LOGGER.warning("Primary response formatter failed; using fallback: %s", type(exc).__name__)
            return self.fallback.format(report)
