"""Split TypeScript/JavaScript into symbol-sized chunks and preserve module context."""

import hashlib

from ingestion.beans.decorators import component
from ingestion.chunking_strategy.code_interface import CodeChunkingStrategy
from ingestion.chunking_strategy.implementation.section_chunker import SectionChunker
from ingestion.domain.models import Chunk, MetadataValue, RawDocument
from ingestion.extractor.code.implementation.typescript_dependency_parser import (
    TypeScriptDependencyParser,
)


@component(contract=CodeChunkingStrategy, name="syntax_aware")
class SyntaxAwareCodeChunker:
    """Keep declared functions, methods, and components intact where the size allows."""

    _SUPPORTED_LANGUAGES = {"typescript", "tsx", "javascript", "jsx"}

    def __init__(self) -> None:
        self._parser = TypeScriptDependencyParser()
        self._fallback = SectionChunker()

    def chunk(self, document: RawDocument, *, max_chars: int) -> tuple[Chunk, ...]:
        path = document.metadata.get("path")
        language = document.metadata.get("language")
        if not isinstance(path, str) or language not in self._SUPPORTED_LANGUAGES:
            return self._fallback.chunk(document, max_chars=max_chars)

        analysis = self._parser.parse(path, document.content)
        if not analysis.symbols:
            return self._fallback.chunk(document, max_chars=max_chars)

        lines = document.content.splitlines(keepends=True)
        regions: list[tuple[int, int, dict[str, MetadataValue]]] = []
        for symbol in analysis.symbols:
            start = symbol.line - 1
            end = symbol.end_line
            if symbol.kind == "Class":
                first_member = min(
                    (
                        member.line - 1
                        for member in analysis.symbols
                        if member.id != symbol.id
                        and start < member.line - 1 < end
                        and member.end_line <= end
                    ),
                    default=end,
                )
                if first_member < end:
                    end = first_member
            called = tuple(
                sorted(
                    {
                        f"{reference.relationship}:{reference.target_name}"
                        for reference in analysis.references
                        if reference.source_symbol_id == symbol.id
                    }
                )
            )
            regions.append(
                (
                    start,
                    end,
                    {
                        "symbol_id": symbol.id,
                        "symbol_name": symbol.name,
                        "symbol_kind": symbol.kind,
                        "start_line": symbol.line,
                        "end_line": symbol.end_line,
                        "called_symbols": called,
                    },
                )
            )

        chunks: list[Chunk] = []
        covered: set[int] = set()
        for start, end, metadata in sorted(regions, key=lambda region: (region[0], region[1])):
            if start >= end or start >= len(lines):
                continue
            end = min(end, len(lines))
            covered.update(range(start, end))
            content = "".join(lines[start:end]).strip()
            if content:
                chunks.extend(self._make_chunks(document, content, metadata, max_chars))

        context_start: int | None = None
        for line_number in range(len(lines) + 1):
            if line_number < len(lines) and line_number not in covered:
                if context_start is None:
                    context_start = line_number
                continue
            if context_start is not None:
                context = "".join(lines[context_start:line_number]).strip()
                if context:
                    chunks.extend(
                        self._make_chunks(
                            document,
                            context,
                            {
                                "symbol_kind": "module_context",
                                "start_line": context_start + 1,
                                "end_line": line_number,
                            },
                            max_chars,
                        )
                    )
                context_start = None
        return tuple(chunks)

    @staticmethod
    def _make_chunks(
        document: RawDocument,
        content: str,
        symbol_metadata: dict[str, MetadataValue],
        max_chars: int,
    ) -> tuple[Chunk, ...]:
        pieces: list[str] = []
        remaining = content
        while remaining:
            if len(remaining) <= max_chars:
                pieces.append(remaining)
                break
            boundary = remaining.rfind("\n", 0, max_chars + 1)
            if boundary < 1:
                boundary = max_chars
            pieces.append(remaining[:boundary].rstrip())
            remaining = remaining[boundary:].lstrip("\n")

        result: list[Chunk] = []
        for part_index, piece in enumerate(pieces):
            if not piece:
                continue
            metadata: dict[str, MetadataValue] = {**document.metadata, **symbol_metadata}
            if len(pieces) > 1:
                metadata["symbol_part"] = part_index + 1
                metadata["symbol_parts"] = len(pieces)
            digest = hashlib.sha256(
                f"{document.source_id}:{symbol_metadata.get('symbol_id', 'context')}:{part_index}:{piece}".encode()
            ).hexdigest()
            result.append(
                Chunk(
                    id=digest,
                    source_id=document.source_id,
                    content=piece,
                    metadata=metadata,
                )
            )
        return tuple(result)
