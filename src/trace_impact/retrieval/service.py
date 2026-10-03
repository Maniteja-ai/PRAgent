"""Orchestration and boundary validation; no provider SDK or storage logic."""

from time import perf_counter

from trace_impact.retrieval.interfaces import EvidenceSelector, Reranker, Retriever
from trace_impact.retrieval.models import Ranking, RetrievalResult, RetrievalScope


class RetrievalService:
    def __init__(
        self, retriever: Retriever, reranker: Reranker, selector: EvidenceSelector, candidate_limit: int = 10
    ):
        if not 1 <= candidate_limit <= 100:
            raise ValueError("Candidate limit must be between 1 and 100")
        self.retriever, self.reranker, self.selector = retriever, reranker, selector
        self.candidate_limit = candidate_limit

    def run(self, query: str, scope: RetrievalScope) -> RetrievalResult:
        query = query.strip()
        if not query or len(query) > 4000:
            raise ValueError("Query must contain 1 to 4000 characters")
        timings = {}
        start = perf_counter()
        candidates = tuple(self.retriever.retrieve(query, scope, self.candidate_limit))
        timings["retrieve"] = (perf_counter() - start) * 1000
        if len(candidates) > self.candidate_limit or len({p.id for p in candidates}) != len(candidates):
            raise ValueError("Retriever exceeded its limit or returned duplicate IDs")
        if any(p.scope != scope for p in candidates):
            raise ValueError("Retriever returned evidence outside the requested scope")
        # Plugins receive copies so mutable metadata cannot alter preserved evidence.
        candidates = tuple(p.model_copy(deep=True) for p in candidates)
        start = perf_counter()
        ranking = self.reranker.rerank(query, tuple(p.model_copy(deep=True) for p in candidates))
        timings["rerank"] = (perf_counter() - start) * 1000
        by_id = {p.id: p for p in candidates}
        if len(ranking.scores) != len(candidates) or {s.id for s in ranking.scores} != set(by_id):
            raise ValueError("Reranker must score each candidate exactly once, with no new IDs")
        if any(s.quote and s.quote not in by_id[s.id].text for s in ranking.scores):
            raise ValueError("Reranker quote is not present in its source passage")
        original_order = {p.id: i for i, p in enumerate(candidates)}
        ranking = Ranking(
            score_kind=ranking.score_kind,
            usage=ranking.usage,
            scores=tuple(sorted(ranking.scores, key=lambda s: (-s.score, original_order[s.id]))),
        )
        start = perf_counter()
        selection = self.selector.select(
            query, tuple(p.model_copy(deep=True) for p in candidates), ranking.model_copy(deep=True)
        )
        timings["select"] = (perf_counter() - start) * 1000
        if len(set(selection.ids)) != len(selection.ids) or not set(selection.ids) <= set(by_id):
            raise ValueError("Selector returned duplicate or unknown IDs")
        expected_order = tuple(s.id for s in ranking.scores if s.id in selection.ids)
        if selection.ids != expected_order:
            raise ValueError("Selector must preserve the validated ranking order")
        return RetrievalResult(
            query=query,
            scope=scope,
            candidates=candidates,
            ranking=ranking,
            selection=selection,
            status="EVIDENCE_FOUND" if selection.ids else "NO_EVIDENCE",
            timings_ms=timings,
        )


def build_retrieval_service(retriever, config, components):
    # Validate all providers and options before constructing any external client.
    for registry, stage in ((components.rerankers, config.reranker), (components.selectors, config.selector)):
        registry.require(stage)
        registry.validate_options(stage.provider, stage.options)
    return RetrievalService(
        retriever,
        components.rerankers.resolve(config.reranker),
        components.selectors.resolve(config.selector),
        config.candidate_limit,
    )
