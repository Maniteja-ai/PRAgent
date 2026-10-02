"""Dependency contracts owned by the application, implemented by adapters."""

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from ..domain.models import (
    Candidate,
    Chunk,
    Corpus,
    Extraction,
    ExtractionRun,
    Project,
    Requirement,
    Snapshot,
    Source,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class SourceReader(Protocol):
    def read(self, source: Source, project: Project, config_dir: Path) -> tuple[bytes, str]: ...


class DocumentProcessor(Protocol):
    def process(
        self, source: Source, project: Project, config_dir: Path, run_dir: Path
    ) -> tuple[Snapshot, list[Chunk]]: ...


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


class ArtifactRepository(Protocol):
    def read(self, path: Path, model: type[ModelT]) -> ModelT: ...
    def write(self, path: Path, value: BaseModel | dict[str, Any]) -> None: ...
    def exists(self, path: Path) -> bool: ...
    def lock(self, run_dir: Path) -> AbstractContextManager: ...


class EventSink(Protocol):
    def emit(self, event: str, **fields: str | int | float) -> None: ...


class GraphRepository(Protocol):
    def initialize(self) -> None: ...
    def load(self, corpus: Corpus, extraction: ExtractionRun | None = None) -> None: ...
    def counts(self, project_id: str) -> dict: ...
    def close(self) -> None: ...
