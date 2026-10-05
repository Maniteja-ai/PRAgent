"""Small typed records shared by ingestion stages."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


MetadataValue = str | int | float | bool | tuple[str, ...]


class RawDocument(Record):
    source_id: str
    content: str
    media_type: str
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)


class Chunk(Record):
    id: str
    source_id: str
    content: str
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)


class VectorRecord(Record):
    id: str
    vector: tuple[float, ...]
    content: str
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)


class GraphRecord(Record):
    id: str
    kind: str
    properties: dict[str, str] = Field(default_factory=dict)


class GraphRelationship(Record):
    id: str
    source_id: str
    target_id: str
    kind: str
    properties: dict[str, str] = Field(default_factory=dict)


class CodeGraph(Record):
    nodes: tuple[GraphRecord, ...] = ()
    relationships: tuple[GraphRelationship, ...] = ()
    source_documents: tuple[RawDocument, ...] = ()


class UiObservation(Record):
    id: str
    url: str
    query_parameter_names: tuple[str, ...] = ()
    confirmed_code_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    page_title: str = ""
    visible_text: str = ""
    content_sha256: str = ""
    http_status: int = 0
    captured: bool = False
    content_ready: bool = False


class ConfirmedMapping(Record):
    """A validated code-to-UI relationship that is safe to publish."""

    id: str
    source_id: str
    target_id: str
    relationship: Literal["AFFECTS_UI"] = "AFFECTS_UI"
    basis: Literal[
        "framework_route", "component_tag", "static_import_reachability"
    ] = "framework_route"
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class Requirement(Record):
    id: str
    statement: str = Field(min_length=1)
    source_chunk_id: str
    evidence: str = Field(min_length=1)


class IngestionResult(Record):
    documents: int
    chunks: int
    vectors: int
    graph_records: int
    graph_relationships: int
    ui_observations: int
    confirmed_mappings: int
    requirements: int
    code_files: int = 0
    code_chunks: int = 0


class CodeUiRefreshResult(Record):
    refresh_id: str
    revision: str
    code_files: int
    import_relationships: int
    ui_observations: int
    ready_ui_observations: int
    confirmed_mappings: int
    refreshed_at: datetime


class CodeIndexResult(Record):
    """Counts and revision for a code-only vector and dependency-graph refresh."""

    run_id: str
    revision: str
    code_files: int
    code_symbols: int
    chunks: int
    vectors: int
    vector_dimensions: int
    relationships: int
    completed_at: datetime


class IngestionFailure(Record):
    error_type: str
    message: str
