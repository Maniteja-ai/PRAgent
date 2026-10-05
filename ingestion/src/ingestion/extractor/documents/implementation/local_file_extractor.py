from pathlib import Path

from ingestion.beans.decorators import component
from ingestion.config_loader.models import DocumentSourceConfig
from ingestion.domain.models import RawDocument
from ingestion.extractor.documents.interface import DocumentExtractor


@component(contract=DocumentExtractor, name="local_file")
class LocalFileExtractor:
    def extract(self, source: DocumentSourceConfig) -> RawDocument:
        path = Path(source.location).resolve(strict=True)
        return RawDocument(
            source_id=source.id,
            content=path.read_text(encoding="utf-8-sig"),
            media_type="text/plain",
            metadata={"location": str(path), "loader": source.loader},
        )
