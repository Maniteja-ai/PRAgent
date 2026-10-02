"""Shared contracts. No Saleor-specific behavior belongs in this module."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

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
    location: str
    format: Literal["html", "markdown"]
    authority: Literal["frontend_spec", "backend_contract", "api_contract"]
    version: str
    scope: list[str]


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

    @model_validator(mode="after")
    def distinct_sources(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique within a project")
        return self


def load_project(path: Path) -> Project:
    return Project.model_validate_json(path.read_text(encoding="utf-8-sig"))


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
