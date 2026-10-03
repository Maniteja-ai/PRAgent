"""Separate persistence for unconfirmed mappings and explicit coverage gaps."""

from pathlib import Path

from trace_impact.ingestion.mappings.models import MappingBatch
from trace_impact.ingestion.storage.artifact_store import FileArtifactRepository


class JsonMappingCandidateStore:
    def __init__(self, directory: Path):
        self._directory = directory
        self._artifacts = FileArtifactRepository()

    def write(self, batch: MappingBatch) -> Path:
        path = self._directory / batch.project_id / f"{batch.run_id}.json"
        self._artifacts.write(path, batch)
        return path

    def read(self, project_id: str, run_id: str) -> MappingBatch:
        return self._artifacts.read(self._directory / project_id / f"{run_id}.json", MappingBatch)
