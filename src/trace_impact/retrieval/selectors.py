"""Selectors."""

from trace_impact.retrieval.models import Selection


class TopKSelector:
    def __init__(self, options):
        self.options = options

    def select(self, query, candidates, ranking):
        return Selection(
            ids=tuple(s.id for s in ranking.scores[: self.options.max_results]),
            reason="fixed_top_k",
            limit_reached=len(ranking.scores) > self.options.max_results,
        )


class ScoreThresholdSelector:
    def __init__(self, options):
        self.options = options

    def select(self, query, candidates, ranking):
        if ranking.score_kind != self.options.score_kind:
            raise ValueError("Selector threshold uses a different score scale from the reranker")
        by_id = {p.id: p for p in candidates}
        eligible, seen_texts = [], set()
        for score in ranking.scores:
            if score.score < self.options.min_score:
                continue
            text = by_id[score.id].text
            if self.options.deduplicate_text and text in seen_texts:
                continue
            seen_texts.add(text)
            eligible.append(score.id)
        return Selection(
            ids=tuple(eligible[: self.options.max_results]),
            reason="relevance_threshold" if eligible else "no_candidate_above_threshold",
            limit_reached=len(eligible) > self.options.max_results,
        )
