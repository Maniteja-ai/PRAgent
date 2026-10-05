"""Port for creating a graph query from the current schema and PR context."""

from typing import Protocol

from impact_agent.domain.models import PullRequestSnapshot


class GraphQueryPlanner(Protocol):
    """Create a parameterized Cypher query for a pull request and graph schema."""

    def create_query(
        self,
        pull_request: PullRequestSnapshot,
        schema: str,
        indexed_revision: str,
        maximum_rows: int,
        maximum_call_depth: int,
    ) -> str: ...

    def close(self) -> None: ...
