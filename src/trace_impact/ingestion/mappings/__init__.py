"""Candidate mapping and coverage-gap contracts."""

from trace_impact.ingestion.mappings.models import CoverageGap, MappingBatch, MappingCandidate
from trace_impact.ingestion.mappings.stable_tags import (
    ObservedImpactElement,
    StableImpactTagMapper,
    StableTagMappingResult,
    UiObservation,
)
from trace_impact.ingestion.mappings.store import JsonMappingCandidateStore

__all__ = [
    "CoverageGap",
    "JsonMappingCandidateStore",
    "MappingBatch",
    "MappingCandidate",
    "ObservedImpactElement",
    "StableImpactTagMapper",
    "StableTagMappingResult",
    "UiObservation",
]
