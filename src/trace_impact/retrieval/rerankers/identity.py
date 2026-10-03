"""Identity."""

from trace_impact.retrieval.models import PassageScore, Ranking


class IdentityReranker:
    def rerank(self, query, candidates):
        return Ranking(
            score_kind="retrieval-score-v1",
            scores=tuple(PassageScore(id=p.id, score=p.retrieval_score) for p in candidates),
        )
