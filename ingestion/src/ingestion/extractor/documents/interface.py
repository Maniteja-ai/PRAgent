from typing import Protocol

from ingestion.config_loader.models import DocumentSourceConfig
from ingestion.domain.models import RawDocument


class DocumentExtractor(Protocol):
    def extract(self, source: DocumentSourceConfig) -> RawDocument: ...
