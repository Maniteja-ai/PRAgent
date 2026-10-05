"""Combine vector search and graph relationships into one evidence list."""

from dataclasses import dataclass

from impact_agent.domain.models import Evidence, PullRequestSnapshot
from impact_agent.tools.knowledge.interface.knowledge_retriever import KnowledgeRetriever


@dataclass(frozen=True, slots=True)
class CompositeKnowledgeRetriever(KnowledgeRetriever):
    retrievers: tuple[KnowledgeRetriever, ...]
    max_evidence: int

    def retrieve(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        mapped_evidence: list[Evidence] = []
        evidence_by_retriever: list[list[Evidence]] = []
        coverage_gaps: list[Evidence] = []
        seen_ids: set[str] = set()
        for retriever in self.retrievers:
            retriever_evidence: list[Evidence] = []
            for evidence in retriever.retrieve(pull_request):
                if evidence.evidence_id in seen_ids:
                    continue
                seen_ids.add(evidence.evidence_id)
                if evidence.coverage_gap is not None:
                    coverage_gaps.append(evidence)
                    continue
                if evidence.confirmed_code_ui_mappings:
                    mapped_evidence.append(evidence)
                else:
                    retriever_evidence.append(evidence)
            evidence_by_retriever.append(retriever_evidence)

        prioritized = mapped_evidence[: self.max_evidence]
        remaining_slots = self.max_evidence - len(prioritized)
        positions = [0] * len(evidence_by_retriever)
        while remaining_slots > 0:
            added_evidence = False
            for index, retriever_evidence in enumerate(evidence_by_retriever):
                position = positions[index]
                if position >= len(retriever_evidence):
                    continue
                prioritized.append(retriever_evidence[position])
                positions[index] += 1
                remaining_slots -= 1
                added_evidence = True
                if remaining_slots == 0:
                    break
            if not added_evidence:
                break
        return tuple(prioritized + coverage_gaps)
