"""Validation for the Neo4j connection used by the knowledge graph retriever."""

from typing import Literal

from pydantic import Field

from impact_agent.config.validation.common import StrictSettings


class GraphDatabaseConfig(StrictSettings):
    provider: Literal["neo4j", "disabled"] = "neo4j"
    enabled: bool = True
    uri_env: str = "NEO4J_URI"
    username_env: str = "NEO4J_USERNAME"
    password_env: str = "NEO4J_PASSWORD"
    database_env: str = "NEO4J_DATABASE"
    indexed_revision: str = "1b5d6545d34fda38c1fb24712ed4bf3dffc684b8"
    max_query_rows: int = Field(default=100, ge=1, le=1000)
    max_call_depth: int = Field(default=3, ge=1, le=5)
    query_timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_schema_items: int = Field(default=300, ge=10, le=2000)
    schema_cache_seconds: int = Field(default=300, ge=0, le=3600)
