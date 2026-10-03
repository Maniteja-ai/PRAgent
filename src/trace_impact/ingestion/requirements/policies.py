"""Deterministic domain rules; no provider, database, or filesystem dependencies."""

import json
import re

from trace_impact.ingestion.models import Candidate, Chunk, Evidence, Requirement, Snapshot, stable_id


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_candidate(
    candidate: Candidate, chunk: Chunk, snapshot: Snapshot, project_id: str
) -> Requirement:
    quote = normalized(candidate.evidence_quote)
    reasons = []
    status = "GROUNDED_CANDIDATE"
    if len(quote) < 12 or quote not in normalized(chunk.text):
        status = "REJECTED"
        reasons.append("Supporting quote is too short or absent from the cited chunk")
    if not all(
        normalized(value)
        for value in [candidate.statement, candidate.actor, candidate.behavior, candidate.expected_outcome]
    ):
        status = "REJECTED"
        reasons.append("Required behavior fields are empty")
    if candidate.support == "inferred":
        reasons.append("Inferred expectation needs human review")
    if snapshot.authority != "frontend_spec" and candidate.layer == "frontend":
        reasons.append("Backend/API source cannot independently establish a frontend requirement")
    reasons.extend(candidate.uncertainty)
    if reasons and status != "REJECTED":
        status = "NEEDS_REVIEW"
    # IDs include conditions, exceptions and source evidence; similar wording alone is not deduplicated.
    rid = stable_id(project_id, candidate.model_dump_json(), chunk.id)
    return Requirement(
        id=rid,
        candidate=candidate,
        evidence=[Evidence(chunk_id=chunk.id, quote=quote)],
        validation=status,
        review_reasons=reasons,
    )


def deduplicate(requirements: list[Requirement]) -> list[Requirement]:
    # Only identical fields/status within a run are combined; retain every supporting passage.
    grouped: dict[str, Requirement] = {}
    identities: dict[str, set[str]] = {}
    for req in requirements:
        data = req.candidate.model_dump(exclude={"evidence_quote"})
        key = json.dumps([data, req.validation, req.review_reasons], sort_keys=True)
        if key not in grouped:
            grouped[key] = req.model_copy(deep=True)
            identities[key] = {req.id}
        else:
            existing = grouped[key]
            for evidence in req.evidence:
                if evidence not in existing.evidence:
                    existing.evidence.append(evidence)
            identities[key].add(req.id)
    for key, existing in grouped.items():
        if len(identities[key]) > 1:
            existing.id = stable_id(*sorted(identities[key]))
        existing.evidence.sort(key=lambda item: (item.chunk_id, item.quote))
    return list(grouped.values())


class GroundingPolicy:
    """Default validation strategy; quote presence is not semantic entailment."""

    version = "grounding-v1"

    def validate(
        self, candidate: Candidate, chunk: Chunk, snapshot: Snapshot, project_id: str
    ) -> Requirement:
        return validate_candidate(candidate, chunk, snapshot, project_id)

    def consolidate(self, requirements: list[Requirement]) -> list[Requirement]:
        return deduplicate(requirements)
