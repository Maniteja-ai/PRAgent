"""Port for retrieving pull request metadata and changed source."""

from typing import Protocol

from impact_agent.domain.models import PullRequestRef, PullRequestSnapshot


class PullRequestProvider(Protocol):
    def fetch(self, reference: PullRequestRef) -> PullRequestSnapshot: ...
