from ingestion.config_loader.models import DocumentSourceConfig
from ingestion.domain.models import RawDocument
from ingestion.extractor.documents.interface import DocumentExtractor


class DocumentExtractorRegistry:
    def __init__(self, extractors: dict[str, DocumentExtractor]) -> None:
        self._extractors = dict(extractors)

    def extract(self, source: DocumentSourceConfig) -> RawDocument:
        try:
            extractor = self._extractors[source.loader]
        except KeyError as exc:
            raise ValueError(f"No document extractor registered for '{source.loader}'") from exc
        return extractor.extract(source)
