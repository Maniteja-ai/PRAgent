"""Compatibility imports; new code uses domain.models."""

from .domain.models import (
    Candidate,
    Chunk,
    Corpus,
    Evidence,
    Extraction,
    ExtractionRun,
    Project,
    Repository,
    Requirement,
    Snapshot,
    Source,
    StrictModel,
    load_project,
    stable_id,
)

__all__ = [
    "Candidate",
    "Chunk",
    "Corpus",
    "Evidence",
    "Extraction",
    "ExtractionRun",
    "Project",
    "Repository",
    "Requirement",
    "Snapshot",
    "Source",
    "StrictModel",
    "load_project",
    "stable_id",
]
