from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ingestion.beans.decorators import component
from ingestion.config_loader.models import DocumentSourceConfig
from ingestion.domain.models import RawDocument
from ingestion.extractor.documents.interface import DocumentExtractor


@component(contract=DocumentExtractor, name="web")
class WebExtractor:
    def extract(self, source: DocumentSourceConfig) -> RawDocument:
        parts = urlsplit(source.location)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("Web extraction requires an HTTPS URL")
        request = Request(  # noqa: S310 - scheme and hostname validated above
            source.location, headers={"User-Agent": "trace-ingestion/1.0"}
        )
        with urlopen(request, timeout=20) as response:  # noqa: S310 - HTTPS enforced above
            content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise ValueError("Web source exceeds the two-megabyte limit")
            media_type = response.headers.get_content_type()
        return RawDocument(
            source_id=source.id,
            content=content.decode("utf-8", errors="replace"),
            media_type=media_type,
            metadata={"location": source.location, "loader": source.loader},
        )
