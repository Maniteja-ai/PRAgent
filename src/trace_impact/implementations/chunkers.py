"""A replaceable chunking strategy using Markdown section boundaries."""

from ..models import Document
from .parsers import make_chunks


class SectionChunker:
    version = "markdown-sections-v1"

    def split(self, document: Document, snapshot_id: str, source_id: str, limit: int):
        return make_chunks(document.text, snapshot_id, source_id, limit)
