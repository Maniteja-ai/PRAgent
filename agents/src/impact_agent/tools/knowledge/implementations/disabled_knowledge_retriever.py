"""Explicit empty retriever used only when knowledge search is disabled in settings."""

from impact_agent.domain.models import Evidence, PullRequestSnapshot
from impact_agent.tools.knowledge.interface.knowledge_retriever import KnowledgeRetriever


class DisabledKnowledgeRetriever(KnowledgeRetriever):
    def retrieve(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        return ()
