"""Public library pipeline: configuration selects interfaces through explicit registries."""

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ._workflows import CollectionService, ExtractionService, GraphPublicationService
from .errors import SourceReadError
from .events import JsonEventSink
from .models import Corpus, Snapshot, load_project, stable_id
from .policies import GroundingPolicy
from .registry import Components


class IngestionPipeline:
    def __init__(self, components: Components):
        self.components = components
        self.events = JsonEventSink(logging.getLogger("trace_impact.events"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.components.close()

    def validate(self, config: Path):
        project = load_project(config)
        for source in project.sources:
            self.components.loaders.require(source.loader_name)
            self.components.parsers.require(source.parser_name)
        for registry, name in [
            (self.components.chunkers, project.chunker),
            (self.components.extractors, project.extractor),
            (self.components.embeddings, project.embedding_provider),
            (self.components.graphs, project.storage.graph),
            (self.components.vectors, project.storage.vector),
            (self.components.artifacts, project.storage.artifacts),
        ]:
            registry.require(name)
        return project

    def collect(self, config: Path, output: Path = Path("runs")):
        project = self.validate(config)
        artifacts = self.components.artifacts.resolve(project.storage.artifacts)
        processor = _ConfiguredProcessor(self.components, artifacts)
        return CollectionService(processor, artifacts, self.events).collect(config, output, project)

    def _run(self, run_dir: Path):
        # Run manifest is the local entry point; full artifacts use the selected store.
        corpus = Corpus.model_validate_json((run_dir / "corpus.json").read_text(encoding="utf-8"))
        return corpus, self.components.artifacts.resolve(corpus.project.storage.artifacts)

    def extract(self, run_dir: Path, max_chunks: int = 100):
        corpus, artifacts = self._run(run_dir)
        extractor = self.components.extractors.resolve(corpus.project.extractor)
        return ExtractionService(extractor, artifacts, GroundingPolicy(), self.events).extract(
            run_dir, max_chunks
        )

    def publish_graph(self, run_dir: Path, with_requirements: bool = False):
        corpus, artifacts = self._run(run_dir)
        graph = self.components.graphs.resolve(corpus.project.storage.graph)
        graph.initialize()
        return GraphPublicationService(artifacts, graph, self.events).publish(run_dir, with_requirements)

    def index(self, run_dir: Path, batch_size: int = 16):
        from ._indexing import VectorIndexWorkflow

        corpus, artifacts = self._run(run_dir)
        embeddings = self.components.embeddings.resolve(corpus.project.embedding_provider)
        vectors = self.components.vectors.resolve(corpus.project.storage.vector)
        return VectorIndexWorkflow(embeddings, vectors, artifacts, self.events).index(run_dir, batch_size)

    def search(self, run_dir: Path, query: str, limit: int = 5):
        from ._indexing import VectorIndexWorkflow

        corpus, artifacts = self._run(run_dir)
        embeddings = self.components.embeddings.resolve(corpus.project.embedding_provider)
        vectors = self.components.vectors.resolve(corpus.project.storage.vector)
        return VectorIndexWorkflow(embeddings, vectors, artifacts, self.events).search(run_dir, query, limit)


class _ConfiguredProcessor:
    def __init__(self, components, artifacts):
        self.components, self.artifacts = components, artifacts

    def process(self, source, project, config_dir, run_dir):
        loader = self.components.loaders.resolve(source.loader_name)
        parser = self.components.parsers.resolve(source.parser_name)
        chunker = self.components.chunkers.resolve(project.chunker)
        version = stable_id(loader.version, parser.version, chunker.version)
        documents, keys = [], set()
        try:
            for raw in loader.load(source, project, config_dir):
                if raw.key in keys:
                    raise ValueError("Loader returned duplicate document keys")
                keys.add(raw.key)
                document = parser.parse(raw)
                raw_hash = hashlib.sha256(raw.content).hexdigest()
                text_hash = hashlib.sha256(document.text.encode()).hexdigest()
                sid = stable_id(
                    project.project_id, source.model_dump_json(), raw.key, version, raw_hash, text_hash
                )
                chunks = chunker.split(document, sid, source.id, project.max_chunk_chars)
                if not chunks or any(c.snapshot_id != sid or c.source_id != source.id for c in chunks):
                    raise ValueError(
                        "Chunker must return nonempty chunks with the supplied source/snapshot IDs"
                    )
                if len({c.id for c in chunks}) != len(chunks):
                    raise ValueError("Chunker returned duplicate chunk IDs")
                folder = run_dir / "sources" / source.id / sid
                raw_path, text_path = folder / "raw.bin", folder / "normalized.md"
                self.artifacts.write_bytes(raw_path, raw.content)
                self.artifacts.write_bytes(text_path, document.text.encode())
                snapshot = Snapshot(
                    id=sid,
                    source_id=source.id,
                    location=source.location or raw.location,
                    resolved_location=raw.location,
                    version=source.version,
                    authority=source.authority,
                    scope=source.scope,
                    retrieved_at=datetime.now(UTC).isoformat(),
                    raw_sha256=raw_hash,
                    normalized_sha256=text_hash,
                    raw_file=raw_path.relative_to(run_dir).as_posix(),
                    text_file=text_path.relative_to(run_dir).as_posix(),
                    document_key=raw.key,
                    processor_version=version,
                )
                documents.append((snapshot, chunks))
            if not documents:
                raise ValueError("Loader returned no documents")
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise SourceReadError(
                "Source could not be loaded, parsed or chunked; check its configuration"
            ) from exc
        return documents


# Existing callers can keep using these convenience functions.
def collect(config: Path, output_root: Path):
    from . import create_pipeline

    with create_pipeline() as pipeline:
        return pipeline.collect(config, output_root)


def extract(run_dir: Path, extractor, max_chunks: int = 100):
    from . import create_pipeline

    with create_pipeline() as pipeline:
        corpus, artifacts = pipeline._run(run_dir)
        return ExtractionService(extractor, artifacts, GroundingPolicy(), pipeline.events).extract(
            run_dir, max_chunks
        )
