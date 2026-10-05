from typing import Protocol

from ingestion.domain.models import IngestionResult


class Pipeline(Protocol):
    def run(self) -> IngestionResult: ...
