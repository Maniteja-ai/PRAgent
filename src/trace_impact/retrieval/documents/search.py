"""Search an exact completed ingestion index with matching embedding profile."""

from pathlib import Path

from trace_impact.ingestion.embeddings.indexer import validate_vector
from trace_impact.ingestion.interfaces import ArtifactStore, EmbeddingProvider, EventSink, VectorStore
from trace_impact.ingestion.models import Corpus, VectorIndexRun
from trace_impact.ingestion.service import require_complete_corpus


class VectorSearchService:
    def __init__(
        self, embeddings: EmbeddingProvider, vectors: VectorStore, artifacts: ArtifactStore, events: EventSink
    ):
        self.embeddings, self.vectors, self.artifacts, self.events = embeddings, vectors, artifacts, events

    def search(self, run_dir: Path, query: str, limit: int = 5):
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("Search requires a nonempty query and a limit between 1 and 100")
        with self.artifacts.lock(run_dir):
            corpus = self.artifacts.read(run_dir / "corpus.json", Corpus)
            require_complete_corpus(corpus)
            index = self.artifacts.read(run_dir / "vector-index.json", VectorIndexRun)
            if (
                index.status != "COMPLETE"
                or index.run_id != corpus.run_id
                or index.project_id != corpus.project.project_id
                or index.profile != self.embeddings.profile
                or set(index.indexed_chunk_ids) != {c.id for c in corpus.chunks}
            ):
                raise ValueError("Search requires a complete index for this exact run and embedding profile")
            vector = self.embeddings.embed_query(query)
            validate_vector(vector, index.profile.dimensions)
            hits = self.vectors.search(index.profile, corpus.project.project_id, corpus.run_id, vector, limit)
            chunks = {c.id: c for c in corpus.chunks}
            snapshots = {s.id: s for s in corpus.snapshots}
            for hit in hits:
                chunk = chunks.get(hit.chunk_id)
                if (
                    chunk is None
                    or hit.source_id != chunk.source_id
                    or hit.text != chunk.text
                    or hit.artifact_path != snapshots[chunk.snapshot_id].text_file
                ):
                    raise ValueError("Vector store returned evidence outside the selected corpus")
            return hits
