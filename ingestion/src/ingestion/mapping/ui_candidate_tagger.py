"""Add explicitly unconfirmed UI route hints to searchable code metadata."""

from typing import Protocol

from ingestion.config_loader.models import UiTaggingConfig
from ingestion.domain.models import CodeGraph


class UiCandidateTagger(Protocol):
    def tag(self, graph: CodeGraph, config: UiTaggingConfig) -> CodeGraph: ...
