"""Existing-index retrieval, baseline ranking, and deterministic evidence selection."""

from trace_impact.retrieval.models import Passage, RetrievalScope


class IndexedCorpusRetriever:
    def __init__(self, pipeline, run_dir, corpus):
        self.pipeline, self.run_dir, self.corpus = pipeline, run_dir, corpus
        self.scope = RetrievalScope(project_id=corpus.project.project_id, run_id=corpus.run_id)

    def retrieve(self, query, scope, limit):
        if scope != self.scope:
            raise ValueError("Requested scope differs from the bound ingestion run")
        hits = self.pipeline.search(self.run_dir, query, limit)
        chunks = {c.id: c for c in self.corpus.chunks}
        snapshots = {s.id: s for s in self.corpus.snapshots}
        # Validate against this bound snapshot as well as the search workflow's current index.
        result = []
        for hit in hits:
            chunk = chunks.get(hit.chunk_id)
            if (
                chunk is None
                or hit.source_id != chunk.source_id
                or hit.text != chunk.text
                or hit.artifact_path != snapshots[chunk.snapshot_id].text_file
            ):
                raise ValueError("Indexed evidence differs from the bound corpus")
            result.append(
                Passage(
                    id=hit.chunk_id,
                    scope=scope,
                    source_id=hit.source_id,
                    text=hit.text,
                    artifact_path=hit.artifact_path,
                    retrieval_score=hit.score,
                    metadata=chunk.metadata,
                )
            )
        return tuple(result)
