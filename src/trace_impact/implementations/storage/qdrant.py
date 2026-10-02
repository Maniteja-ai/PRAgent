"""Qdrant adapter with mandatory project/run filters and profile-separated collections."""

from math import isclose

from qdrant_client import QdrantClient, models

from ...models import EmbeddingProfile, SearchHit, VectorRecord


class QdrantVectorStore:
    def __init__(self, client: QdrantClient):
        self.client = client

    def close(self):
        self.client.close()

    @staticmethod
    def collection(profile: EmbeddingProfile) -> str:
        return "trace_impact_v1_" + profile.id

    def _ensure(self, profile: EmbeddingProfile):
        name = self.collection(profile)
        if not self.client.collection_exists(name):
            self.client.create_collection(
                name,
                vectors_config=models.VectorParams(size=profile.dimensions, distance=models.Distance.COSINE),
            )
        configuration = self.client.get_collection(name).config.params.vectors
        if not isinstance(configuration, models.VectorParams) or configuration.size != profile.dimensions:
            raise ValueError("Vector collection configuration differs from the selected profile")
        if configuration.distance != models.Distance.COSINE:
            raise ValueError("Vector collection must use cosine similarity")
        return name

    def upsert(self, profile: EmbeddingProfile, records: list[VectorRecord]) -> None:
        if any(r.profile_id != profile.id or not r.project_id or not r.run_id for r in records):
            raise ValueError("Vector records require matching profile and explicit project/run scope")
        name = self._ensure(profile)
        self.client.upsert(
            name,
            points=[
                models.PointStruct(id=r.id, vector=r.vector, payload=r.model_dump(exclude={"vector", "id"}))
                for r in records
            ],
            wait=True,
        )

    def verify(self, profile: EmbeddingProfile, records: list[VectorRecord]) -> bool:
        actual = {
            str(point.id): point
            for point in self.client.retrieve(
                self.collection(profile), ids=[r.id for r in records], with_payload=True, with_vectors=True
            )
        }
        for record in records:
            point = actual.get(record.id)
            if point is None or point.payload != record.model_dump(exclude={"vector", "id"}):
                return False
            # Cosine collections normalize vectors, so compare normalized components.
            if not isinstance(point.vector, list) or len(point.vector) != len(record.vector):
                return False
            norm = sum(value * value for value in record.vector) ** 0.5
            if not all(
                isclose(a, b / norm, abs_tol=1e-5) for a, b in zip(point.vector, record.vector, strict=True)
            ):
                return False
        return True

    def search(self, profile, project_id, run_id, vector, limit):
        if not project_id or not run_id:
            raise ValueError("Vector searches require explicit project and run IDs")
        result = self.client.query_points(
            self.collection(profile),
            query=vector,
            limit=limit,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id)),
                    models.FieldCondition(key="run_id", match=models.MatchValue(value=run_id)),
                    models.FieldCondition(key="profile_id", match=models.MatchValue(value=profile.id)),
                ]
            ),
            with_payload=True,
        )
        return [
            SearchHit(
                chunk_id=p.payload["chunk_id"],
                source_id=p.payload["source_id"],
                text=p.payload["text"],
                artifact_path=p.payload["artifact_path"],
                score=p.score,
            )
            for p in result.points
        ]
