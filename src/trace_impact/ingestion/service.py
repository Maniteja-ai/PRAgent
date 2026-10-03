"""Use cases depend on ports and domain rules, never concrete IO/provider libraries."""

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from trace_impact.ingestion.config import Project, load_project
from trace_impact.ingestion.interfaces import (
    ArtifactRepository,
    DocumentProcessor,
    EventSink,
    GraphRepository,
    RequirementExtractor,
    RequirementPolicy,
)
from trace_impact.ingestion.models import Corpus, Extraction, ExtractionRun, stable_id
from trace_impact.shared.errors import ExtractionError, ProviderError, SourceReadError


class CollectionService:
    def __init__(self, processor: DocumentProcessor, artifacts: ArtifactRepository, events: EventSink):
        self.processor, self.artifacts, self.events = processor, artifacts, events

    def collect(self, config: Path, output_root: Path, project: Project | None = None) -> tuple[Path, Corpus]:
        started = monotonic()
        project = project or load_project(config)
        run_id = uuid.uuid4().hex
        run_dir = output_root / project.project_id / run_id
        corpus = Corpus(
            run_id=run_id,
            project=project,
            created_at=datetime.now(UTC).isoformat(),
            config_sha256=hashlib.sha256(project.model_dump_json().encode()).hexdigest(),
            snapshots=[],
            chunks=[],
            errors=[],
        )
        # Writing a manifest creates the run before any network work.
        self.artifacts.write(run_dir / "corpus.json", corpus)
        with self.artifacts.lock(run_dir):
            for source in project.sources:
                try:
                    documents = self.processor.process(source, project, config.parent, run_dir)
                    for snapshot, chunks in documents:
                        corpus.snapshots.append(snapshot)
                        corpus.chunks.extend(chunks)
                    self.events.emit(
                        "source.collected",
                        run_id=run_id,
                        source_id=source.id,
                        chunks=sum(len(chunks) for _, chunks in documents),
                    )
                except SourceReadError as exc:
                    corpus.errors.append({"source_id": source.id, "error_type": exc.code})
                    self.events.emit("source.failed", run_id=run_id, source_id=source.id, code=exc.code)
                # Unexpected exceptions propagate; do not disguise programming or disk errors as source failures.
                self.artifacts.write(run_dir / "corpus.json", corpus)
            self.artifacts.write(
                run_dir / "inventory.json",
                {
                    "project_id": project.project_id,
                    "run_id": run_id,
                    "status": "PARTIAL" if corpus.errors else "COLLECTED",
                    "sources_requested": len(project.sources),
                    "sources_collected": len({s.source_id for s in corpus.snapshots}),
                    "documents": len(corpus.snapshots),
                    "chunks": len(corpus.chunks),
                    "characters": sum(len(c.text) for c in corpus.chunks),
                    "oversized_chunks": [c.id for c in corpus.chunks if c.oversized],
                    "errors": corpus.errors,
                    "sources": [
                        {
                            "source_id": s.source_id,
                            "version": s.version,
                            "normalized_sha256": s.normalized_sha256,
                            "chunks": sum(c.snapshot_id == s.id for c in corpus.chunks),
                        }
                        for s in corpus.snapshots
                    ],
                },
            )
        self.events.emit(
            "collection.finished",
            run_id=run_id,
            sources=len(corpus.snapshots),
            failures=len(corpus.errors),
            elapsed_ms=round((monotonic() - started) * 1000),
        )
        return run_dir, corpus


def require_complete_corpus(corpus: Corpus) -> None:
    expected = {source.id for source in corpus.project.sources}
    actual = {source.source_id for source in corpus.snapshots}
    if corpus.errors or expected != actual:
        raise ValueError("Source collection is partial; fix the sources and collect again before extraction")
    snapshots = {snapshot.id: snapshot for snapshot in corpus.snapshots}
    if len(snapshots) != len(corpus.snapshots) or any(c.snapshot_id not in snapshots for c in corpus.chunks):
        raise ValueError("Corpus contains inconsistent snapshot references")
    if not corpus.chunks or len({c.id for c in corpus.chunks}) != len(corpus.chunks):
        raise ValueError("Corpus must contain unique document chunks")
    if {c.snapshot_id for c in corpus.chunks} != set(snapshots):
        raise ValueError("Every document snapshot must have chunks")
    if any(c.source_id != snapshots[c.snapshot_id].source_id or not c.text.strip() for c in corpus.chunks):
        raise ValueError("Chunks must contain text and match their snapshot source")


class ExtractionService:
    def __init__(
        self,
        extractor: RequirementExtractor,
        artifacts: ArtifactRepository,
        policy: RequirementPolicy,
        events: EventSink,
    ):
        self.extractor, self.artifacts, self.policy, self.events = extractor, artifacts, policy, events

    def extract(self, run_dir: Path, max_chunks: int = 100) -> ExtractionRun:
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        with self.artifacts.lock(run_dir):
            return self._extract_locked(run_dir, max_chunks)

    def _extract_locked(self, run_dir: Path, max_chunks: int) -> ExtractionRun:
        corpus = self.artifacts.read(run_dir / "corpus.json", Corpus)
        require_complete_corpus(corpus)
        snapshots = {s.id: s for s in corpus.snapshots}
        run = ExtractionRun(
            id=uuid.uuid4().hex,
            corpus_run_id=corpus.run_id,
            project_id=corpus.project.project_id,
            provider=self.extractor.provider,
            model=self.extractor.model,
            prompt_version=self.extractor.prompt_version,
            created_at=datetime.now(UTC).isoformat(),
            status="PARTIAL",
            processed_chunk_ids=[],
            no_requirement_chunks={},
            errors=[],
            requirements=[],
        )
        self._checkpoint(run_dir, run)
        for chunk in corpus.chunks[:max_chunks]:
            snapshot = snapshots[chunk.snapshot_id]
            cache_key = stable_id(self.extractor.fingerprint, corpus.config_sha256, chunk.id)
            path = run_dir / "extraction-cache" / f"{cache_key}.json"
            try:
                cached = self.artifacts.exists(path)
                if cached:
                    result = self.artifacts.read(path, Extraction)
                else:
                    result = self.extractor.extract(chunk, snapshot, corpus.project.scope)
                if not result.requirements and not (result.no_requirement_reason or "").strip():
                    raise ExtractionError("Empty extraction requires an explanation")
                if result.requirements and result.no_requirement_reason is not None:
                    raise ExtractionError(
                        "Extraction cannot contain requirements and a no-requirement reason"
                    )
                # Validate all candidates before mutating run state or caching a new response.
                validated = [
                    self.policy.validate(c, chunk, snapshot, run.project_id) for c in result.requirements
                ]
                if not cached:
                    self.artifacts.write(path, result)
                run.requirements.extend(validated)
                run.processed_chunk_ids.append(chunk.id)
                if not result.requirements:
                    run.no_requirement_chunks[chunk.id] = result.no_requirement_reason
                self.events.emit(
                    "chunk.extracted",
                    run_id=run.id,
                    chunk_id=chunk.id,
                    cache_hit=int(cached),
                    candidates=len(validated),
                )
            except ProviderError as exc:
                run.errors.append({"chunk_id": chunk.id, "error_type": exc.code})
                self._checkpoint(run_dir, run)
                raise
            except ExtractionError as exc:
                run.errors.append({"chunk_id": chunk.id, "error_type": exc.code})
                self.events.emit("chunk.failed", run_id=run.id, chunk_id=chunk.id, code=exc.code)
            self._checkpoint(run_dir, run)
        if len(run.processed_chunk_ids) == len(corpus.chunks) and not run.errors:
            run.status = "COMPLETE"
        run.requirements = self.policy.consolidate(run.requirements)
        self._checkpoint(run_dir, run)
        self.artifacts.write(
            run_dir / "review.json",
            {
                "extraction_run_id": run.id,
                "status": run.status,
                "requirements": [
                    r.model_dump() for r in run.requirements if r.validation != "GROUNDED_CANDIDATE"
                ],
                "note": "Quote grounding does not prove semantic entailment. Review grounded candidates too.",
            },
        )
        self.events.emit(
            "extraction.finished",
            run_id=run.id,
            status=run.status,
            requirements=len(run.requirements),
            failures=len(run.errors),
        )
        return run

    def _checkpoint(self, run_dir: Path, run: ExtractionRun) -> None:
        self.artifacts.write(run_dir / "extractions" / f"{run.id}.json", run)
        self.artifacts.write(run_dir / "extraction.json", run)


class GraphPublicationService:
    def __init__(self, artifacts: ArtifactRepository, graph: GraphRepository, events: EventSink):
        self.artifacts, self.graph, self.events = artifacts, graph, events

    def publish(self, run_dir: Path, with_requirements: bool = False) -> dict:
        with self.artifacts.lock(run_dir):
            corpus = self.artifacts.read(run_dir / "corpus.json", Corpus)
            require_complete_corpus(corpus)
            extraction = (
                self.artifacts.read(run_dir / "extraction.json", ExtractionRun) if with_requirements else None
            )
            self.graph.load(corpus, extraction)
            counts = self.graph.counts(corpus.project.project_id)
            self.events.emit("graph.published", run_id=corpus.run_id, project_id=corpus.project.project_id)
            return counts
