"""Port for retrieving relevant indexed evidence."""

from typing import Protocol

from impact_agent.domain.models import Evidence, PullRequestSnapshot


class KnowledgeRetriever(Protocol):
    def retrieve(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]: ...
