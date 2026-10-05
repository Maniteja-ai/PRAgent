"""Neo4j implementation for code/UI nodes and confirmed mappings."""

import os
from typing import cast

from neo4j import Driver, GraphDatabase, ManagedTransaction

from ingestion.beans.decorators import component
from ingestion.config_loader.models import StorageConfig
from ingestion.domain.models import ConfirmedMapping, GraphRecord, GraphRelationship
from ingestion.storage.interface import GraphStore


@component(contract=GraphStore, name="neo4j")
class Neo4jGraphStore:
    def __init__(self, config: StorageConfig) -> None:
        connection = config.graph.connection
        if connection is None:
            raise ValueError("storage.graph.connection is required for the neo4j provider")
        try:
            uri = os.environ[connection.uri_env]
            username = os.environ[connection.username_env]
            password = os.environ[connection.password_env]
            database = os.environ[connection.database_env]
        except KeyError as exc:
            raise ValueError(f"Required Neo4j environment variable is missing: {exc.args[0]}") from exc
        self._database = database
        self._driver: Driver = GraphDatabase.driver(uri, auth=(username, password))

    def save(self, records: tuple[GraphRecord, ...]) -> int:
        if not records:
            return 0
        rows = [record.model_dump(mode="json") for record in records]
        self._driver.execute_query(
            """
            UNWIND $rows AS row
            MERGE (entity:IngestedEntity {id: row.id})
            SET entity.kind = row.kind,
                entity += row.properties
            """,
            rows=rows,
            database_=self._database,
        )
        return len(records)

    def save_mappings(self, mappings: tuple[ConfirmedMapping, ...]) -> int:
        if not mappings:
            return 0
        rows = [mapping.model_dump(mode="json") for mapping in mappings]
        self._driver.execute_query(
            """
            UNWIND $rows AS row
            MATCH (source:IngestedEntity {id: row.source_id})
            MATCH (target:IngestedEntity {id: row.target_id})
            MERGE (source)-[mapping:CONFIRMED_MAPPING {id: row.id}]->(target)
            SET mapping.relationship = row.relationship,
                mapping.basis = row.basis,
                mapping.confidence = row.confidence,
                mapping.evidence_ids = row.evidence_ids
            """,
            rows=rows,
            database_=self._database,
        )
        return len(mappings)

    def replace_mappings(
        self, target_ids: tuple[str, ...], mappings: tuple[ConfirmedMapping, ...]
    ) -> int:
        if not target_ids:
            return self.save_mappings(mappings)
        rows = [mapping.model_dump(mode="json") for mapping in mappings]
        with self._driver.session(database=self._database) as session:

            def replace(transaction: ManagedTransaction) -> int:
                transaction.run(
                    """
                    UNWIND $target_ids AS target_id
                    MATCH (target:IngestedEntity {id: target_id})
                    OPTIONAL MATCH (source:IngestedEntity)-[mapping:CONFIRMED_MAPPING]->(target)
                    DELETE mapping
                    """,
                    target_ids=list(target_ids),
                )
                if rows:
                    transaction.run(
                        """
                        UNWIND $rows AS row
                        MATCH (source:IngestedEntity {id: row.source_id})
                        MATCH (target:IngestedEntity {id: row.target_id})
                        MERGE (source)-[mapping:CONFIRMED_MAPPING {id: row.id}]->(target)
                        SET mapping.relationship = row.relationship,
                            mapping.basis = row.basis,
                            mapping.confidence = row.confidence,
                            mapping.evidence_ids = row.evidence_ids
                        """,
                        rows=rows,
                    )
                return len(rows)

            return cast(int, session.execute_write(replace))

    def save_relationships(self, relationships: tuple[GraphRelationship, ...]) -> int:
        if not relationships:
            return 0
        rows = [item.model_dump(mode="json") for item in relationships]
        self._driver.execute_query(
            """
            UNWIND $rows AS row
            MATCH (source:IngestedEntity {id: row.source_id})
            MATCH (target:IngestedEntity {id: row.target_id})
            MERGE (source)-[relation:CODE_RELATIONSHIP {id: row.id}]->(target)
            SET relation.kind = row.kind,
                relation += row.properties
            """,
            rows=rows,
            database_=self._database,
        )
        return len(relationships)

    def replace_code_graph(
        self,
        records: tuple[GraphRecord, ...],
        relationships: tuple[GraphRelationship, ...],
    ) -> int:
        """Replace static code dependencies while retaining UI pages and mappings."""
        record_rows = [record.model_dump(mode="json") for record in records]
        relationship_rows = [item.model_dump(mode="json") for item in relationships]
        file_ids = [record.id for record in records if record.kind == "CodeFile"]
        with self._driver.session(database=self._database) as session:

            def replace(transaction: ManagedTransaction) -> int:
                transaction.run(
                    """
                    MATCH (entity:IngestedEntity)
                    WHERE entity.kind = 'CodeSymbol'
                    DETACH DELETE entity
                    """
                )
                transaction.run(
                    "MATCH ()-[relation:CODE_RELATIONSHIP]->() DELETE relation"
                )
                transaction.run(
                    """
                    MATCH (entity:IngestedEntity)
                    WHERE entity.kind IN ['Route', 'RouteParameter']
                    DETACH DELETE entity
                    """
                )
                transaction.run(
                    """
                    MATCH (entity:IngestedEntity)
                    WHERE entity.kind = 'CodeFile' AND NOT entity.id IN $file_ids
                    DETACH DELETE entity
                    """,
                    file_ids=file_ids,
                )
                if record_rows:
                    transaction.run(
                        """
                        UNWIND $rows AS row
                        MERGE (entity:IngestedEntity {id: row.id})
                        SET entity.kind = row.kind,
                            entity += row.properties
                        """,
                        rows=record_rows,
                    )
                if relationship_rows:
                    transaction.run(
                        """
                        UNWIND $rows AS row
                        MATCH (source:IngestedEntity {id: row.source_id})
                        MATCH (target:IngestedEntity {id: row.target_id})
                        MERGE (source)-[relation:CODE_RELATIONSHIP {id: row.id}]->(target)
                        SET relation.kind = row.kind,
                            relation += row.properties
                        """,
                        rows=relationship_rows,
                    )
                return len(record_rows) + len(relationship_rows)

            return cast(int, session.execute_write(replace))

    def close(self) -> None:
        self._driver.close()
