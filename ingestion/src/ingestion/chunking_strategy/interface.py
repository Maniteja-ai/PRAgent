from typing import Protocol

from ingestion.domain.models import Chunk, RawDocument


class ChunkingStrategy(Protocol):
    def chunk(self, document: RawDocument, *, max_chars: int) -> tuple[Chunk, ...]: ...
