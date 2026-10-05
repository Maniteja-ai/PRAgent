"""Code-chunking adapter for repositories without AST-based chunking."""

from ingestion.beans.decorators import component
from ingestion.chunking_strategy.code_interface import CodeChunkingStrategy
from ingestion.chunking_strategy.implementation.section_chunker import SectionChunker
from ingestion.domain.models import Chunk, RawDocument


@component(contract=CodeChunkingStrategy, name="section")
class SectionCodeChunker:
    def chunk(self, document: RawDocument, *, max_chars: int) -> tuple[Chunk, ...]:
        return SectionChunker().chunk(document, max_chars=max_chars)
