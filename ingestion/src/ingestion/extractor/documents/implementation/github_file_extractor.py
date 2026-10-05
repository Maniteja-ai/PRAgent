from ingestion.beans.decorators import component
from ingestion.extractor.documents.implementation.web_extractor import WebExtractor
from ingestion.extractor.documents.interface import DocumentExtractor


@component(contract=DocumentExtractor, name="github_file")
class GitHubFileExtractor(WebExtractor):
    """Load a pinned GitHub raw-file URL using the bounded HTTPS extractor."""
