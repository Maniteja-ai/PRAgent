"""Shared contracts. No Saleor-specific behavior belongs in this module."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from trace_impact.ingestion.config import Project
from trace_impact.shared.contracts import StrictModel


def stable_id(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:32]


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
    metadata: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda v: not v)


class Chunk(StrictModel):
    id: str
    snapshot_id: str
    source_id: str
    heading: str
    ordinal: int
    text: str
    oversized: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda v: not v)


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
    metadata: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda v: not v)


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
