"""Mappings stay reviewable until evidence validation confirms them."""

from typing import Literal

from pydantic import Field, model_validator

from trace_impact.shared.contracts import Contract

EntityKind = Literal["CodeSymbol", "UIElement", "UserFlow", "Requirement"]
RelationshipKind = Literal["RENDERS", "CONTAINS", "CHECKS"]


class EntityReference(Contract):
    kind: EntityKind
    id: str = Field(min_length=1)


class MappingEvidence(Contract):
    source: Literal["code", "ui", "document", "human_review"]
    reference: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    detail: str = Field(min_length=1)


class MappingCandidate(Contract):
    id: str = Field(min_length=1)
    source: EntityReference
    target: EntityReference
    relationship: RelationshipKind
    confidence: float = Field(ge=0, le=1)
    status: Literal["CANDIDATE", "CONFIRMED", "REJECTED"] = "CANDIDATE"
    evidence: tuple[MappingEvidence, ...] = Field(min_length=1)
    reviewed_by: str | None = None

    @model_validator(mode="after")
    def require_review_for_terminal_status(self) -> "MappingCandidate":
        if self.status != "CANDIDATE" and not self.reviewed_by:
            raise ValueError("Confirmed or rejected mappings require reviewed_by")
        return self


class CoverageGap(Contract):
    id: str = Field(min_length=1)
    area: str = Field(min_length=1)
    missing_relationship: RelationshipKind
    reason: str = Field(min_length=1)
    affected_entity_ids: tuple[str, ...] = ()


class MappingBatch(Contract):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    candidates: tuple[MappingCandidate, ...] = ()
    coverage_gaps: tuple[CoverageGap, ...] = ()

    @model_validator(mode="after")
    def unique_ids(self) -> "MappingBatch":
        identifiers = [item.id for item in self.candidates]
        identifiers.extend(item.id for item in self.coverage_gaps)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Mapping candidate and coverage-gap IDs must be unique")
        return self
