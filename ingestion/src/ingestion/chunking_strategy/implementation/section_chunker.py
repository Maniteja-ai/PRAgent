import hashlib

from ingestion.beans.decorators import component
from ingestion.chunking_strategy.interface import ChunkingStrategy
from ingestion.domain.models import Chunk, RawDocument


@component(contract=ChunkingStrategy, name="section")
class SectionChunker:
    def chunk(self, document: RawDocument, *, max_chars: int) -> tuple[Chunk, ...]:
        sections = [part.strip() for part in document.content.split("\n\n") if part.strip()]
        chunks: list[Chunk] = []
        for section in sections:
            for start in range(0, len(section), max_chars):
                content = section[start : start + max_chars]
                digest = hashlib.sha256(
                    f"{document.source_id}:{len(chunks)}:{content}".encode()
                ).hexdigest()
                chunks.append(
                    Chunk(
                        id=digest,
                        source_id=document.source_id,
                        content=content,
                        metadata=document.metadata,
                    )
                )
        return tuple(chunks)
