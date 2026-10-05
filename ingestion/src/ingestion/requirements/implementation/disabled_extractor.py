from ingestion.beans.decorators import component
from ingestion.domain.models import Chunk, Requirement
from ingestion.requirements.interface import RequirementExtractor


@component(contract=RequirementExtractor, name="none")
class DisabledRequirementExtractor:
    def extract(self, chunks: tuple[Chunk, ...]) -> tuple[Requirement, ...]:
        return ()

    def close(self) -> None:
        return None
