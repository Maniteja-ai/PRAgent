"""Application inputs and provider configuration for ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from trace_impact.ingestion.metadata import MetadataConfig
from trace_impact.shared.contracts import StrictModel
from trace_impact.shared.names import ComponentName


class Repository(StrictModel):
    url: str
    baseline_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    code_roots: list[str] = Field(default_factory=lambda: ["src"])


class Source(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    location: str = ""
    loader: ComponentName | None = None
    parser: ComponentName | None = None
    # Kept for reading v1 project/run artifacts. New configurations use parser.
    format: ComponentName | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    authority: Literal["frontend_spec", "backend_contract", "api_contract"]
    version: str
    scope: list[str]
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        exclude_if=lambda v: not v,
        description="Custom values defined by the project's metadata schema.",
    )

    @property
    def loader_name(self) -> str:
        return self.loader or ("web" if self.location.startswith(("http://", "https://")) else "local_file")

    @property
    def parser_name(self) -> str:
        if self.parser and self.format and self.parser != self.format:
            raise ValueError("parser and legacy format disagree")
        return self.parser or self.format or "markdown"


class StorageConfig(StrictModel):
    graph: ComponentName = "neo4j"
    vector: ComponentName = "qdrant"
    artifacts: ComponentName = "local"


class ExtractionConfig(StrictModel):
    provider: ComponentName
    model: str = Field(min_length=1)
    max_output_tokens: int = Field(default=6000, ge=256, le=32000)
    thinking_level: Literal["low", "medium", "high"] | None = None
    requests_per_minute: int = Field(default=10, ge=0, le=10000)
    options: dict[str, Any] = Field(
        default_factory=dict,
        exclude_if=lambda v: not v,
        description="Options declared by the selected provider implementation.",
    )


class EmbeddingConfig(StrictModel):
    provider: ComponentName
    model: str = Field(min_length=1)
    dimensions: int = Field(gt=0, le=65536)
    requests_per_minute: int = Field(default=10, ge=0, le=10000)
    options: dict[str, Any] = Field(
        default_factory=dict,
        exclude_if=lambda v: not v,
        description="Options declared by the selected provider implementation.",
    )


class Project(StrictModel):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    project_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str
    repository: Repository
    baseline_url: str
    scope: list[str] = Field(min_length=1)
    allowed_document_hosts: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(min_length=1)
    max_chunk_chars: int = Field(default=8000, ge=1000, le=32000)
    excluded_inputs: list[str] = Field(default_factory=list)
    chunker: ComponentName = "section"
    extractor: ComponentName | ExtractionConfig = "langchain"
    embedding_provider: ComponentName | EmbeddingConfig = "openai"
    storage: StorageConfig = Field(default_factory=StorageConfig)
    metadata: MetadataConfig | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description="Define custom field types, descriptions and shared defaults, then set source overrides.",
    )

    @model_validator(mode="after")
    def distinct_sources(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique within a project")
        for source in self.sources:
            if self.metadata is not None:
                self.metadata.for_source(source.metadata)
            elif source.metadata:
                raise ValueError("Define project metadata fields before supplying source metadata")
        return self


def load_project(path: Path) -> Project:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        return Project.model_validate(yaml.safe_load(text))
    return Project.model_validate_json(text)
