"""Interfaces."""

from typing import Protocol

from trace_impact.retrieval.models import Passage, Ranking, RetrievalScope, Selection


class Retriever(Protocol):
    def retrieve(self, query: str, scope: RetrievalScope, limit: int) -> tuple[Passage, ...]: ...


class Reranker(Protocol):
    def rerank(self, query: str, candidates: tuple[Passage, ...]) -> Ranking: ...


class EvidenceSelector(Protocol):
    def select(self, query: str, candidates: tuple[Passage, ...], ranking: Ranking) -> Selection: ...
