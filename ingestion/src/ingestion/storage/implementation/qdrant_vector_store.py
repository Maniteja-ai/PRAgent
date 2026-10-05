"""Qdrant vector-store implementation with configuration and dimension checks."""

import os
import uuid

from qdrant_client import QdrantClient, models

from ingestion.beans.decorators import component
from ingestion.config_loader.models import ApplicationConfig, StorageConfig
from ingestion.domain.models import VectorRecord
from ingestion.storage.interface import VectorStore


@component(contract=VectorStore, name="qdrant")
class QdrantVectorStore:
    def __init__(self, config: ApplicationConfig, storage: StorageConfig) -> None:
        connection = storage.vector.connection
        if connection is None:
            raise ValueError("storage.vector.connection is required for Qdrant")
        if connection.url_env is not None and os.getenv(connection.url_env):
            api_key = os.environ[connection.api_key_env] if connection.api_key_env else None
            self._client = QdrantClient(url=os.environ[connection.url_env], api_key=api_key)
        elif connection.path is not None:
            connection.path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(connection.path))
        else:
            raise ValueError("Configured Qdrant URL is missing and no local path was provided")
        self._collection = connection.collection
        self._distance = {
            "cosine": models.Distance.COSINE,
            "dot": models.Distance.DOT,
            "euclidean": models.Distance.EUCLID,
        }[connection.similarity]
        self._project_id = config.project.id
        self._revision = config.input.repository.baseline_commit

    def save(self, records: tuple[VectorRecord, ...]) -> int:
        if not records:
            return 0
        dimensions = len(records[0].vector)
        if dimensions == 0 or any(len(record.vector) != dimensions for record in records):
            raise ValueError("Embedding provider returned inconsistent vector dimensions")
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                self._collection,
                vectors_config=models.VectorParams(
                    size=dimensions,
                    distance=self._distance,
                ),
            )
        self._client.upsert(
            collection_name=self._collection,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, record.id)),
                    vector=list(record.vector),
                    payload={
                        "record_id": record.id,
                        "project_id": self._project_id,
                        "revision": self._revision,
                        "content": record.content,
                        "metadata": record.metadata,
                    },
                )
                for record in records
            ],
            wait=True,
        )
        return len(records)

    def replace_code_records(self, records: tuple[VectorRecord, ...]) -> int:
        """Upsert this source revision, then remove stale code for this project."""
        if records:
            self.save(records)
        elif not self._client.collection_exists(self._collection):
            return 0
        self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="project_id", match=models.MatchValue(value=self._project_id)
                        ),
                        models.FieldCondition(
                            key="metadata.kind", match=models.MatchValue(value="code")
                        ),
                    ],
                    must_not=(
                        [
                            models.FieldCondition(
                                key="revision", match=models.MatchValue(value=self._revision)
                            )
                        ]
                        if records
                        else []
                    ),
                )
            ),
            wait=True,
        )
        return len(records)

    def close(self) -> None:
        self._client.close()
