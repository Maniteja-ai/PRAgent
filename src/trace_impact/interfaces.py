"""Dependency contracts owned by the application, implemented by adapters."""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from trace_impact.models import (
    Candidate,
    Chunk,
    Corpus,
    Document,
    EmbeddingProfile,
    Extraction,
    ExtractionRun,
    Project,
    RawDocument,
    Requirement,
    SearchHit,
    Snapshot,
    Source,
    VectorRecord,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class SourceLoader(Protocol):
    version: str

    def load(self, source: Source, project: Project, config_dir: Path) -> Iterable[RawDocument]: ...


class DocumentParser(Protocol):
    version: str

    def parse(self, raw: RawDocument) -> Document: ...


class Chunker(Protocol):
    version: str

    def split(self, document: Document, snapshot_id: str, source_id: str, limit: int) -> list[Chunk]: ...


class DocumentProcessor(Protocol):
    def process(
        self, source: Source, project: Project, config_dir: Path, run_dir: Path
    ) -> list[tuple[Snapshot, list[Chunk]]]: ...


class RequirementExtractor(Protocol):
    provider: str
    model: str
    fingerprint: str
    prompt_version: str

    def extract(self, chunk: Chunk, snapshot: Snapshot, scope: list[str]) -> Extraction: ...


class RequirementPolicy(Protocol):
    def validate(
        self, candidate: Candidate, chunk: Chunk, snapshot: Snapshot, project_id: str
    ) -> Requirement: ...
    def consolidate(self, requirements: list[Requirement]) -> list[Requirement]: ...


class ArtifactStore(Protocol):
    def read(self, path: Path, model: type[ModelT]) -> ModelT: ...
    def write(self, path: Path, value: BaseModel | dict[str, Any]) -> None: ...
    def exists(self, path: Path) -> bool: ...
    def lock(self, run_dir: Path) -> AbstractContextManager: ...
    def write_bytes(self, path: Path, data: bytes) -> None: ...


class EventSink(Protocol):
    def emit(self, event: str, **fields: str | int | float) -> None: ...


class GraphStore(Protocol):
    def initialize(self) -> None: ...
    def load(self, corpus: Corpus, extraction: ExtractionRun | None = None) -> None: ...
    def counts(self, project_id: str) -> dict: ...
    def close(self) -> None: ...


class EmbeddingProvider(Protocol):
    profile: EmbeddingProfile

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class VectorStore(Protocol):
    def upsert(self, profile: EmbeddingProfile, records: list[VectorRecord]) -> None: ...
    def verify(self, profile: EmbeddingProfile, records: list[VectorRecord]) -> bool: ...
    def search(
        self, profile: EmbeddingProfile, project_id: str, run_id: str, vector: list[float], limit: int
    ) -> list[SearchHit]: ...


# Compatibility names for the existing checkpoint and graph workflows.
ArtifactRepository = ArtifactStore
GraphRepository = GraphStore
