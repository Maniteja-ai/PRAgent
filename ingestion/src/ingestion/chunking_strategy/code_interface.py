"""Contract for syntax-aware chunks produced from source code."""

from typing import Protocol

from ingestion.domain.models import Chunk, RawDocument


class CodeChunkingStrategy(Protocol):
    def chunk(self, document: RawDocument, *, max_chars: int) -> tuple[Chunk, ...]: ...
