"""Composable retrieval: inject stages directly or build from registered JSON selections."""

from trace_impact.retrieval.config import RetrievalConfig, load_retrieval_config
from trace_impact.retrieval.interfaces import EvidenceSelector, Reranker, Retriever
from trace_impact.retrieval.models import (
    Passage,
    PassageScore,
    Ranking,
    RetrievalResult,
    RetrievalScope,
    Selection,
)
from trace_impact.retrieval.service import RetrievalService, build_retrieval_service
from trace_impact.shared.stage_config import StageConfig

__all__ = [
    "EvidenceSelector",
    "Passage",
    "PassageScore",
    "Ranking",
    "RetrievalConfig",
    "RetrievalResult",
    "RetrievalScope",
    "Retriever",
    "Reranker",
    "RetrievalService",
    "Selection",
    "StageConfig",
    "build_retrieval_service",
    "load_retrieval_config",
]
