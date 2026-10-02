"""Compatibility import; dependency construction lives in bootstrap."""

from .infrastructure.neo4j_store import Neo4jStore

__all__ = ["Neo4jStore"]
