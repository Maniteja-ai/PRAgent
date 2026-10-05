"""Typed configuration beans loaded from one user-facing JSON file."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigBean(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ProjectConfig(ConfigBean):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1)


class StageConfig(ConfigBean):
    provider: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class UiTaggingConfig(ConfigBean):
    """Controls conservative, static code-to-route candidate tags."""

    enabled: bool = True
    max_dependency_hops: int = Field(default=12, ge=1, le=50)
    max_routes_per_file: int = Field(default=10, ge=1, le=100)


class UiJourneyActionConfig(ConfigBean):
    action: Literal["open", "click", "wait_visible"]
    path: str | None = None
    selector: str | None = None

    @model_validator(mode="after")
    def validate_action(self) -> UiJourneyActionConfig:
        if self.action == "open":
            if not self.path or self.selector is not None or not self.path.startswith("/"):
                raise ValueError("open journey action requires an absolute path and no selector")
            if self.path.startswith("//"):
                raise ValueError("Journey paths must stay on the configured host")
        elif not self.selector or self.path is not None:
            raise ValueError(f"{self.action} journey action requires only a selector")
        return self


class UiJourneyConfig(ConfigBean):
    journey_id: str = Field(min_length=1, max_length=100)
    actions: tuple[UiJourneyActionConfig, ...] = Field(min_length=1, max_length=30)


class RepositoryConfig(ConfigBean):
    url: str
    baseline_commit: str
    code_roots: tuple[str, ...] = ()


class DocumentSourceConfig(ConfigBean):
    id: str
    location: str
    loader: str
    parser: str | None = None
    authority: str | None = None
    version: str | None = None
    scope: tuple[str, ...] = ()
    options: dict[str, Any] = Field(default_factory=dict)


class DocumentsConfig(ConfigBean):
    allowed_hosts: tuple[str, ...] = ()
    sources: tuple[DocumentSourceConfig, ...] = ()
    metadata: dict[str, Any] | None = None


class CodeInputConfig(ConfigBean):
    enabled: bool = True
    repository_path: Path
    revision: str
    analyzer: StageConfig = Field(default_factory=lambda: StageConfig(provider="typescript"))
    ui_tagging: UiTaggingConfig = Field(default_factory=UiTaggingConfig)


class UiInputConfig(ConfigBean):
    enabled: bool = False
    base_url: str
    seed_paths: tuple[str, ...] = ("/",)
    journeys: tuple[UiJourneyConfig, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    explorer: StageConfig = Field(default_factory=lambda: StageConfig(provider="recorded"))
    mapping_resolver: StageConfig = Field(default_factory=lambda: StageConfig(provider="evidence"))
    max_pages: int = Field(default=50, ge=1)
    max_actions_per_page: int = Field(default=20, ge=1)


class InputConfig(ConfigBean):
    repository: RepositoryConfig
    baseline_url: str
    scope: tuple[str, ...]
    excluded_inputs: tuple[str, ...] = ()
    documents: DocumentsConfig
    code: CodeInputConfig | None = None
    ui: UiInputConfig | None = None


class ModelInput(ConfigBean):
    provider: str
    name: str
    api_key_env: str | None = None
    thinking_level: str | None = None
    max_output_tokens: int | None = None
    requests_per_minute: int = Field(default=10, ge=1)


class RequirementExtractionConfig(ConfigBean):
    enabled: bool = True
    implementation: str
    model: ModelInput | None = None
    max_chunks: int = Field(default=1000, ge=1)
    batch_size: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def require_model_for_llm(self) -> RequirementExtractionConfig:
        if self.enabled and self.implementation == "gemini" and self.model is None:
            raise ValueError("Gemini requirement extraction requires model input")
        return self


class EmbeddingConfig(ConfigBean):
    enabled: bool = True
    implementation: str
    model: ModelInput | None = None
    task_type: str | None = None
    batch_size: int = Field(default=16, ge=1)

    @model_validator(mode="after")
    def require_model_for_external_provider(self) -> EmbeddingConfig:
        if self.enabled and self.implementation in {"gemini", "fastembed"} and self.model is None:
            raise ValueError(f"{self.implementation} embeddings require model input")
        if self.enabled and self.implementation == "gemini" and self.model is not None:
            if self.model.name == "gemini-embedding-001" and self.task_type != "RETRIEVAL_DOCUMENT":
                raise ValueError("gemini-embedding-001 requires task_type=RETRIEVAL_DOCUMENT")
            if self.model.name == "gemini-embedding-2" and self.task_type is not None:
                raise ValueError("gemini-embedding-2 uses document text formatting, not task_type")
            if self.model.name not in {"gemini-embedding-001", "gemini-embedding-2"}:
                raise ValueError("Unsupported Gemini embedding model")
        return self


class EvaluationJudgeConfig(ConfigBean):
    enabled: bool = False
    implementation: str = "gemini"
    model: ModelInput | None = None
    temperature: float = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def require_model_when_enabled(self) -> EvaluationJudgeConfig:
        if self.enabled and self.model is None:
            raise ValueError("Enabled evaluation judge requires model input")
        return self


class ModelsConfig(ConfigBean):
    embedding: EmbeddingConfig
    requirement_extraction: RequirementExtractionConfig
    evaluation_judge: EvaluationJudgeConfig = Field(default_factory=EvaluationJudgeConfig)


class ChunkingStrategyConfig(ConfigBean):
    provider: Literal["section", "fixed_size"] = "section"
    code_provider: Literal["section", "syntax_aware"] = "syntax_aware"
    max_chunk_chars: int = Field(default=8000, ge=100, le=100000)


class ArtifactStorageConfig(ConfigBean):
    provider: str = "local"
    run_directory: Path


class Neo4jConnectionConfig(ConfigBean):
    uri_env: str = Field(min_length=1)
    username_env: str = Field(min_length=1)
    password_env: str = Field(min_length=1)
    database_env: str = Field(min_length=1)


class GraphStorageConfig(ConfigBean):
    provider: str = "memory"
    connection: Neo4jConnectionConfig | None = None
    publish_documents: bool = True
    publish_requirements: bool = True
    publish_code: bool = True
    publish_confirmed_mappings: bool = True


class QdrantConnectionConfig(ConfigBean):
    url_env: str | None = None
    api_key_env: str | None = None
    path: Path | None = None
    collection: str
    similarity: Literal["cosine", "dot", "euclidean"] = "cosine"

    @model_validator(mode="after")
    def require_remote_or_local(self) -> QdrantConnectionConfig:
        if self.url_env is None and self.path is None:
            raise ValueError("Qdrant connection requires url_env or path")
        return self


class VectorStorageConfig(ConfigBean):
    provider: str = "memory"
    connection: QdrantConnectionConfig | None = None
    publish_chunks: bool = True


class StorageConfig(ConfigBean):
    artifacts: ArtifactStorageConfig
    graph: GraphStorageConfig
    vector: VectorStorageConfig


class RetryConfig(ConfigBean):
    attempts: int = Field(default=2, ge=0, le=5)


class TimeoutConfig(ConfigBean):
    run_seconds: int = Field(default=1800, ge=1)
    model_seconds: int = Field(default=45, ge=1)


class LimitConfig(ConfigBean):
    documents: int = Field(default=1000, ge=1)
    code_files: int = Field(default=10000, ge=1)
    ui_pages: int = Field(default=50, ge=1)
    confirmed_mappings: int = Field(default=10000, ge=1)


class FailureConfig(ConfigBean):
    source_error: Literal["continue", "stop"] = "continue"


class ConstraintsConfig(ConfigBean):
    retries: RetryConfig = Field(default_factory=RetryConfig)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    limits: LimitConfig = Field(default_factory=LimitConfig)
    failures: FailureConfig = Field(default_factory=FailureConfig)


class EvaluationRecordingConfig(ConfigBean):
    enabled: bool = True
    provider: str = "jsonl"
    directory: Path | None = None
    include_payloads: bool = False


class EvaluationMetricsConfig(ConfigBean):
    requirement_grounding: bool = False
    vector_retrieval: bool = False
    graph_retrieval: bool = False
    mapping_precision: bool = False
    mapping_recall: bool = False


class EvaluationConfig(ConfigBean):
    recording: EvaluationRecordingConfig
    dataset_manifest: Path | None = Field(
        default=None,
        description="Path to the evaluation dataset manifest, relative to evaluation.json.",
    )
    metrics: EvaluationMetricsConfig = Field(default_factory=EvaluationMetricsConfig)


class ConfigFiles(ConfigBean):
    input: Path
    models: Path
    chunking: Path
    storage: Path
    constraints: Path
    evaluation: Path


class ApplicationManifest(ConfigBean):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    env_file: str | None = None
    project: ProjectConfig
    files: ConfigFiles


class ApplicationConfig(ConfigBean):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    env_file: str | None = None
    project: ProjectConfig
    input: InputConfig
    models: ModelsConfig
    chunking: ChunkingStrategyConfig = Field(default_factory=ChunkingStrategyConfig)
    storage: StorageConfig
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
    evaluation: EvaluationConfig
