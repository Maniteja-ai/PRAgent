"""Shared contracts. No Saleor-specific behavior belongs in this module."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def stable_id(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Repository(StrictModel):
    url: str
    baseline_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    code_roots: list[str] = Field(default_factory=lambda: ["src"])


class Source(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    location: str = ""
    loader: str | None = None
    parser: str | None = None
    # Kept for reading v1 project/run artifacts. New configurations use parser.
    format: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    authority: Literal["frontend_spec", "backend_contract", "api_contract"]
    version: str
    scope: list[str]

    @property
    def loader_name(self) -> str:
        return self.loader or ("web" if self.location.startswith(("http://", "https://")) else "local_file")

    @property
    def parser_name(self) -> str:
        if self.parser and self.format and self.parser != self.format:
            raise ValueError("parser and legacy format disagree")
        return self.parser or self.format or "markdown"


class StorageConfig(StrictModel):
    graph: str = "neo4j"
    vector: str = "qdrant"
    artifacts: str = "local"


class Project(StrictModel):
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
    chunker: str = "section"
    extractor: str = "langchain"
    embedding_provider: str = "openai"
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @model_validator(mode="after")
    def distinct_sources(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique within a project")
        return self


def load_project(path: Path) -> Project:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        return Project.model_validate(yaml.safe_load(text))
    return Project.model_validate_json(text)


class RawDocument(StrictModel):
    content: bytes
    location: str
    key: str = "document"


class Document(StrictModel):
    text: str = Field(min_length=1)


class Snapshot(StrictModel):
    id: str
    source_id: str
    location: str
    resolved_location: str
    version: str
    authority: str
    scope: list[str]
    retrieved_at: str
    raw_sha256: str
    normalized_sha256: str
    raw_file: str
    text_file: str
    document_key: str = "document"
    processor_version: str = "legacy"


class Chunk(StrictModel):
    id: str
    snapshot_id: str
    source_id: str
    heading: str
    ordinal: int
    text: str
    oversized: bool = False


class Candidate(StrictModel):
    statement: str
    actor: str
    behavior: str
    preconditions: list[str]
    expected_outcome: str
    exceptions: list[str]
    layer: Literal["frontend", "backend", "api"]
    support: Literal["documented", "inferred"]
    evidence_quote: str
    uncertainty: list[str]


class Extraction(StrictModel):
    requirements: list[Candidate]
    no_requirement_reason: str | None


class Evidence(StrictModel):
    chunk_id: str
    quote: str


class Requirement(StrictModel):
    id: str
    candidate: Candidate
    evidence: list[Evidence]
    validation: Literal["GROUNDED_CANDIDATE", "NEEDS_REVIEW", "REJECTED"]
    review_reasons: list[str]


class Corpus(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    project: Project
    created_at: str
    config_sha256: str
    snapshots: list[Snapshot]
    chunks: list[Chunk]
    errors: list[dict[str, str]]


class ExtractionRun(StrictModel):
    schema_version: Literal[1] = 1
    id: str
    corpus_run_id: str
    project_id: str
    provider: str
    model: str
    prompt_version: str
    created_at: str
    status: Literal["COMPLETE", "PARTIAL"]
    processed_chunk_ids: list[str]
    no_requirement_chunks: dict[str, str]
    errors: list[dict[str, str]]
    requirements: list[Requirement]


class EmbeddingProfile(StrictModel):
    provider: str
    model: str
    dimensions: int = Field(gt=0)
    preprocessing: str = "plain-chunk-v1"

    @property
    def id(self) -> str:
        return stable_id(self.model_dump_json())


class VectorRecord(StrictModel):
    id: str
    project_id: str
    run_id: str
    profile_id: str
    chunk_id: str
    source_id: str
    heading: str
    text: str
    artifact_path: str
    vector: list[float]


class SearchHit(StrictModel):
    chunk_id: str
    source_id: str
    text: str
    score: float
    artifact_path: str


class VectorIndexRun(StrictModel):
    run_id: str
    project_id: str
    profile: EmbeddingProfile
    status: Literal["PARTIAL", "COMPLETE"]
    indexed_chunk_ids: list[str]
