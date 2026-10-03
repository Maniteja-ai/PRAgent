"""Small vendor-independent contracts. Passage identity survives every stage."""

from typing import Any, Literal

from pydantic import Field

from trace_impact.shared.contracts import Contract


class RetrievalScope(Contract):
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class Passage(Contract):
    id: str = Field(min_length=1)
    scope: RetrievalScope
    source_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    artifact_path: str = Field(min_length=1)
    retrieval_score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class PassageScore(Contract):
    id: str = Field(min_length=1)
    score: float
    quote: str = ""


class Ranking(Contract):
    score_kind: str = Field(min_length=1)
    scores: tuple[PassageScore, ...]
    usage: dict[str, int] = Field(default_factory=dict)


class Selection(Contract):
    ids: tuple[str, ...]
    reason: str = Field(min_length=1)
    limit_reached: bool = False


class RetrievalResult(Contract):
    query: str
    scope: RetrievalScope
    candidates: tuple[Passage, ...]
    ranking: Ranking
    selection: Selection
    status: Literal["EVIDENCE_FOUND", "NO_EVIDENCE"]
    coverage: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    timings_ms: dict[str, float]
