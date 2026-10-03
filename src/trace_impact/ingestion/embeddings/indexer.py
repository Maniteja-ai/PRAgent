"""Private vector workflow shared by the library and CLI; no vendor SDK dependencies."""

import math
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel

from trace_impact.ingestion.interfaces import ArtifactStore, EmbeddingProvider, EventSink, VectorStore
from trace_impact.ingestion.models import Corpus, VectorIndexRun, VectorRecord, stable_id
from trace_impact.ingestion.service import require_complete_corpus


class _CachedEmbedding(BaseModel):
    profile_id: str
    text_hash: str
    vector: list[float]


def validate_vector(vector: list[float], dimensions: int):
    if len(vector) != dimensions or not all(math.isfinite(v) for v in vector) or not any(vector):
        raise ValueError("Embedding must be finite, nonzero and match its configured dimensions")


class VectorIndexer:
    def __init__(
        self, embeddings: EmbeddingProvider, vectors: VectorStore, artifacts: ArtifactStore, events: EventSink
    ):
        self.embeddings, self.vectors, self.artifacts, self.events = embeddings, vectors, artifacts, events

    def index(self, run_dir: Path, batch_size: int = 16) -> VectorIndexRun:
        if not 1 <= batch_size <= 128:
            raise ValueError("Embedding batch_size must be between 1 and 128")
        with self.artifacts.lock(run_dir):
            corpus = self.artifacts.read(run_dir / "corpus.json", Corpus)
            require_complete_corpus(corpus)
            profile = self.embeddings.profile
            manifest = VectorIndexRun(
                run_id=corpus.run_id,
                project_id=corpus.project.project_id,
                profile=profile,
                status="PARTIAL",
                indexed_chunk_ids=[],
            )
            self.artifacts.write(run_dir / "vector-index.json", manifest)
            snapshots = {s.id: s for s in corpus.snapshots}
            for start in range(0, len(corpus.chunks), batch_size):
                batch = corpus.chunks[start : start + batch_size]
                texts = [chunk.heading + "\n\n" + chunk.text for chunk in batch]
                hashes = [stable_id(text) for text in texts]
                paths = [run_dir / "embedding-cache" / f"{stable_id(profile.id, h)}.json" for h in hashes]
                cached = [
                    self.artifacts.read(p, _CachedEmbedding) if self.artifacts.exists(p) else None
                    for p in paths
                ]
                missing = [i for i, item in enumerate(cached) if item is None]
                if missing:
                    values = self.embeddings.embed_documents([texts[i] for i in missing])
                    if len(values) != len(missing):
                        raise ValueError("Embedding provider returned a different batch length")
                    for i, vector in zip(missing, values, strict=True):
                        validate_vector(vector, profile.dimensions)
                        cached[i] = _CachedEmbedding(
                            profile_id=profile.id, text_hash=hashes[i], vector=vector
                        )
                        self.artifacts.write(paths[i], cached[i])
                records = []
                for chunk, item, text_hash in zip(batch, cached, hashes, strict=True):
                    if item.profile_id != profile.id or item.text_hash != text_hash:
                        raise ValueError("Embedding cache identity mismatch")
                    validate_vector(item.vector, profile.dimensions)
                    records.append(
                        VectorRecord(
                            id=str(
                                uuid5(
                                    NAMESPACE_URL,
                                    stable_id(corpus.project.project_id, corpus.run_id, chunk.id, profile.id),
                                )
                            ),
                            project_id=corpus.project.project_id,
                            run_id=corpus.run_id,
                            profile_id=profile.id,
                            chunk_id=chunk.id,
                            source_id=chunk.source_id,
                            heading=chunk.heading,
                            text=chunk.text,
                            artifact_path=snapshots[chunk.snapshot_id].text_file,
                            vector=item.vector,
                            metadata=chunk.metadata,
                        )
                    )
                self.vectors.upsert(profile, records)
                if not self.vectors.verify(profile, records):
                    raise ValueError("Vector records did not pass read-back verification")
                manifest.indexed_chunk_ids.extend(c.id for c in batch)
                self.artifacts.write(run_dir / "vector-index.json", manifest)
                self.events.emit(
                    "vectors.indexed",
                    run_id=corpus.run_id,
                    records=len(records),
                    embedded=len(missing),
                    cache_hits=len(batch) - len(missing),
                )
            manifest.status = "COMPLETE"
            self.artifacts.write(run_dir / "vector-index.json", manifest)
            return manifest
