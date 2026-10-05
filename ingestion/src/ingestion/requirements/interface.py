from typing import Protocol

from ingestion.domain.models import Chunk, Requirement


class RequirementExtractor(Protocol):
    def extract(self, chunks: tuple[Chunk, ...]) -> tuple[Requirement, ...]: ...

    def close(self) -> None: ...
