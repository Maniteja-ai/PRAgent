"""A replaceable chunking strategy using Markdown section boundaries."""

from trace_impact.ingestion.documents.parsers import make_chunks
from trace_impact.ingestion.models import Document


class SectionChunker:
    version = "markdown-sections-v1"

    def split(self, document: Document, snapshot_id: str, source_id: str, limit: int):
        return make_chunks(document.text, snapshot_id, source_id, limit)
