from ingestion.beans.decorators import component
from ingestion.chunking_strategy.implementation.section_chunker import SectionChunker
from ingestion.chunking_strategy.interface import ChunkingStrategy
from ingestion.domain.models import Chunk, RawDocument


@component(contract=ChunkingStrategy, name="fixed_size")
class FixedSizeChunker:
    def chunk(self, document: RawDocument, *, max_chars: int) -> tuple[Chunk, ...]:
        return SectionChunker().chunk(
            document.model_copy(update={"content": document.content.replace("\n\n", "\n")}),
            max_chars=max_chars,
        )
