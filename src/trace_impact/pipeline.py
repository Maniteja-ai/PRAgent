"""Public library pipeline: configuration selects interfaces through explicit registries."""

import logging
from pathlib import Path

from trace_impact.ingestion.config import load_project
from trace_impact.ingestion.documents.processor import ConfiguredDocumentProcessor
from trace_impact.ingestion.models import Corpus
from trace_impact.ingestion.requirements.policies import GroundingPolicy
from trace_impact.ingestion.service import CollectionService, ExtractionService, GraphPublicationService
from trace_impact.shared.events import JsonEventSink
from trace_impact.shared.registry import Components


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
        return self.validate_project(project)

    def validate_project(self, project):
        for source in project.sources:
            self.components.loaders.require(source.loader_name)
            self.components.parsers.require(source.parser_name)
            self.components.loaders.validate_options(source.loader_name, source.options)
        for registry, name in [
            (self.components.chunkers, project.chunker),
            (self.components.extractors, project.extractor),
            (self.components.embeddings, project.embedding_provider),
            (self.components.graphs, project.storage.graph),
            (self.components.vectors, project.storage.vector),
            (self.components.artifacts, project.storage.artifacts),
        ]:
            registry.require(name)
            if not isinstance(name, str):
                registry.validate_options(name.provider, name.options)
        return project

    def collect(self, config: Path, output: Path = Path("runs")):
        project = self.validate(config)
        artifacts = self.components.artifacts.resolve(project.storage.artifacts)
        processor = ConfiguredDocumentProcessor(self.components, artifacts)
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
        from trace_impact.ingestion.embeddings.indexer import VectorIndexer

        corpus, artifacts = self._run(run_dir)
        embeddings = self.components.embeddings.resolve(corpus.project.embedding_provider)
        vectors = self.components.vectors.resolve(corpus.project.storage.vector)
        return VectorIndexer(embeddings, vectors, artifacts, self.events).index(run_dir, batch_size)

    def search(self, run_dir: Path, query: str, limit: int = 5):
        from trace_impact.retrieval.documents.search import VectorSearchService

        corpus, artifacts = self._run(run_dir)
        embeddings = self.components.embeddings.resolve(corpus.project.embedding_provider)
        vectors = self.components.vectors.resolve(corpus.project.storage.vector)
        return VectorSearchService(embeddings, vectors, artifacts, self.events).search(run_dir, query, limit)

    def retrieve(self, run_dir: Path, query: str, config=None):
        """Default to vector top five; model reranking requires explicit JSON selection."""
        from trace_impact.retrieval import RetrievalConfig, build_retrieval_service, load_retrieval_config
        from trace_impact.retrieval.documents.adapters import IndexedCorpusRetriever

        if isinstance(config, (str, Path)):
            config = load_retrieval_config(Path(config))
        if config is None:
            config = RetrievalConfig(candidate_limit=5)
        corpus, _ = self._run(run_dir)
        retriever = IndexedCorpusRetriever(self, run_dir, corpus)
        service = build_retrieval_service(retriever, config, self.components)
        return service.run(query, retriever.scope)

    def analyze_code(self, config: Path):
        from trace_impact.ingestion.code.config import load_code_config

        selected = load_code_config(config)
        self.components.code_analyzers.require(selected.analyzer)
        self.components.code_analyzers.validate_options(selected.analyzer.provider, selected.analyzer.options)
        return self.components.code_analyzers.resolve(selected.analyzer).analyze(selected)

    def _code_graph(self, graph_id):
        from trace_impact.ingestion.storage.neo4j_code_store import Neo4jCodeStore

        store = self.components.graphs.resolve("neo4j")
        return Neo4jCodeStore(store.driver, store.database, graph_id)

    def publish_code_graph(self, snapshot):
        graph = self._code_graph(snapshot.id)
        graph.initialize()
        return graph.publish(snapshot)

    def retrieve_impact(self, graph_id, query):
        from trace_impact.retrieval.graph.neo4j_reader import Neo4jGraphReader
        from trace_impact.retrieval.graph.service import ImpactRetriever

        store = self.components.graphs.resolve("neo4j")
        reader = Neo4jGraphReader(store.driver, store.database, graph_id)
        return ImpactRetriever(reader).retrieve(query)


# Existing callers can keep using these convenience functions.
def collect(config: Path, output_root: Path):
    from trace_impact import create_pipeline

    with create_pipeline() as pipeline:
        return pipeline.collect(config, output_root)


def extract(run_dir: Path, extractor, max_chunks: int = 100):
    from trace_impact import create_pipeline

    with create_pipeline() as pipeline:
        corpus, artifacts = pipeline._run(run_dir)
        return ExtractionService(extractor, artifacts, GroundingPolicy(), pipeline.events).extract(
            run_dir, max_chunks
        )
