"""Strict contracts for the six human-readable ingestion configuration files."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from trace_impact.ingestion.config import EmbeddingConfig, ExtractionConfig, Repository, Source
from trace_impact.ingestion.metadata import MetadataConfig
from trace_impact.shared.contracts import Contract
from trace_impact.shared.names import ComponentName
from trace_impact.shared.stage_config import StageConfig


class ConfigFileReferences(Contract):
    inputs: str = "inputs.json"
    processing: str = "processing.json"
    storage: str = "storage.json"
    runtime: str = "runtime.json"
    evaluation: str = "evaluation.json"


class ProjectIdentity(Contract):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1)


class IngestionEntry(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[2] = 2
    project: ProjectIdentity
    config: ConfigFileReferences = Field(default_factory=ConfigFileReferences)


class DocumentInputs(Contract):
    allowed_hosts: tuple[str, ...] = ()
    sources: tuple[Source, ...] = ()
    metadata: MetadataConfig | None = None

    @model_validator(mode="after")
    def validate_sources(self) -> DocumentInputs:
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("Document source IDs must be unique")
        for source in self.sources:
            if self.metadata is not None:
                self.metadata.for_source(source.metadata)
            elif source.metadata:
                raise ValueError("Define document metadata fields before using source metadata")
        return self


class CodeInput(Contract):
    enabled: bool = True
    repository_path: str
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")


class UiInput(Contract):
    enabled: bool = False
    base_url: str
    seed_paths: tuple[str, ...] = ("/",)
    allowed_hosts: tuple[str, ...] = ()


class InputConfig(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    repository: Repository
    baseline_url: str
    scope: tuple[str, ...] = Field(min_length=1)
    excluded_inputs: tuple[str, ...] = ()
    documents: DocumentInputs
    code: CodeInput | None = None
    ui: UiInput | None = None


class DocumentProcessing(Contract):
    chunker: ComponentName = "section"
    max_chunk_chars: int = Field(default=8000, ge=1000, le=32000)


class RequirementProcessing(Contract):
    enabled: bool = True
    extractor: ComponentName | ExtractionConfig = "langchain"
    max_chunks: int = Field(default=100, ge=1, le=100000)


class EmbeddingProcessing(Contract):
    enabled: bool = True
    provider: ComponentName | EmbeddingConfig = "openai"
    batch_size: int = Field(default=16, ge=1, le=1024)


class CodeProcessing(Contract):
    analyzer: StageConfig = Field(default_factory=lambda: StageConfig(provider="typescript"))


class UiProcessing(Contract):
    explorer: StageConfig = Field(default_factory=lambda: StageConfig(provider="browser"))
    max_pages: int = Field(default=50, ge=1, le=10000)
    max_actions_per_page: int = Field(default=20, ge=1, le=1000)


class MappingProcessing(Contract):
    proposer: StageConfig = Field(default_factory=lambda: StageConfig(provider="semantic"))
    validator: StageConfig = Field(default_factory=lambda: StageConfig(provider="evidence"))
    minimum_confidence: float = Field(default=0.80, ge=0, le=1)
    require_human_review_below: float = Field(default=0.95, ge=0, le=1)

    @model_validator(mode="after")
    def validate_thresholds(self) -> MappingProcessing:
        if self.require_human_review_below < self.minimum_confidence:
            raise ValueError("Human-review threshold cannot be below minimum confidence")
        return self


class ProcessingConfig(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    documents: DocumentProcessing = Field(default_factory=DocumentProcessing)
    requirements: RequirementProcessing = Field(default_factory=RequirementProcessing)
    embeddings: EmbeddingProcessing = Field(default_factory=EmbeddingProcessing)
    code: CodeProcessing = Field(default_factory=CodeProcessing)
    ui: UiProcessing = Field(default_factory=UiProcessing)
    mappings: MappingProcessing = Field(default_factory=MappingProcessing)


class ArtifactStorage(Contract):
    provider: ComponentName = "local"
    run_directory: str = "../../../runs"


class GraphStorage(Contract):
    provider: ComponentName = "neo4j"
    publish_documents: bool = True
    publish_requirements: bool = True
    publish_code: bool = True
    publish_confirmed_mappings: bool = True


class VectorStorage(Contract):
    provider: ComponentName = "qdrant"
    publish_chunks: bool = True


class CandidateStorage(Contract):
    directory: str = "../../../artifacts/mapping-candidates"


class StorageConfig(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    artifacts: ArtifactStorage = Field(default_factory=ArtifactStorage)
    graph: GraphStorage = Field(default_factory=GraphStorage)
    vector: VectorStorage = Field(default_factory=VectorStorage)
    candidates: CandidateStorage = Field(default_factory=CandidateStorage)


class RetryPolicy(Contract):
    attempts: int = Field(default=2, ge=0, le=5)


class TimeoutPolicy(Contract):
    run_seconds: int = Field(default=1800, ge=1, le=86400)
    model_seconds: int = Field(default=45, ge=1, le=600)


class WorkLimits(Contract):
    documents: int = Field(default=1000, ge=1)
    code_files: int = Field(default=10000, ge=1)
    ui_pages: int = Field(default=50, ge=1)
    mapping_candidates: int = Field(default=10000, ge=1)


class FailurePolicy(Contract):
    source_error: Literal["continue", "stop"] = "continue"


class RuntimeConfig(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    retries: RetryPolicy = Field(default_factory=RetryPolicy)
    timeouts: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    limits: WorkLimits = Field(default_factory=WorkLimits)
    failures: FailurePolicy = Field(default_factory=FailurePolicy)


class RecordingConfig(Contract):
    enabled: bool = True
    directory: str = "../../../artifacts/ingestion-observations"
    include_payloads: bool = False


class MetricConfig(Contract):
    requirement_grounding: bool = True
    vector_retrieval: bool = True
    graph_retrieval: bool = True
    mapping_precision: bool = True
    mapping_recall: bool = True


class EvaluationConfig(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    golden_dataset: str | None = None
    metrics: MetricConfig = Field(default_factory=MetricConfig)


class ConfigurationPaths(Contract):
    entry: Path
    inputs: Path
    processing: Path
    storage: Path
    runtime: Path
    evaluation: Path


class IngestionConfiguration(Contract):
    entry: IngestionEntry
    inputs: InputConfig
    processing: ProcessingConfig
    storage: StorageConfig
    runtime: RuntimeConfig
    evaluation: EvaluationConfig
    paths: ConfigurationPaths

    @model_validator(mode="after")
    def validate_project_identity(self) -> IngestionConfiguration:
        if self.entry.project.id.strip() == "":
            raise ValueError("Project identity is required")
        return self
