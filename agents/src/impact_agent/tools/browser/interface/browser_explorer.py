"""Port for safely observing the deployed storefront."""

from typing import Protocol

from impact_agent.domain.models import Evidence, PullRequestSnapshot


class BrowserExplorer(Protocol):
    def explore(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]: ...
