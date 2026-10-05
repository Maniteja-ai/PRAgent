"""Refresh source-code vectors and the static dependency graph without reingesting docs."""

from datetime import UTC, datetime
from uuid import uuid4

from ingestion.chunking_strategy.code_interface import CodeChunkingStrategy
from ingestion.chunking_strategy.interface import ChunkingStrategy
from ingestion.config_loader.models import ApplicationConfig, CodeInputConfig
from ingestion.domain.models import (
    Chunk,
    CodeGraph,
    CodeIndexResult,
    IngestionFailure,
    VectorRecord,
)
from ingestion.embedding.interface import EmbeddingProvider
from ingestion.evaluation.decorators import record_stage
from ingestion.evaluation.interface import StageRecorder
from ingestion.extractor.code.interface import CodeExtractor
from ingestion.storage.artifacts.interface import ArtifactStore
from ingestion.storage.interface import GraphStore, VectorStore


class CodeIndexPipeline:
    """Embed repository source and refresh its Neo4j dependency relationships."""

    def __init__(
        self,
        config: ApplicationConfig,
        code_extractor: CodeExtractor,
        chunking_strategy: ChunkingStrategy,
        code_chunking_strategy: CodeChunkingStrategy,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        graph_store: GraphStore,
        artifact_store: ArtifactStore,
        stage_recorder: StageRecorder,
    ) -> None:
        self.config = config
        self.code_extractor = code_extractor
        self.chunking_strategy = chunking_strategy
        self.code_chunking_strategy = code_chunking_strategy
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.artifact_store = artifact_store
        self.stage_recorder = stage_recorder
        self.run_id = uuid4().hex

    def __enter__(self) -> "CodeIndexPipeline":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.embedding_provider.close()
        self.vector_store.close()
        self.graph_store.close()

    def run(self) -> CodeIndexResult:
        self.artifact_store.archive_failure()
        self.artifact_store.save_config(self.config)
        try:
            return self._run()
        except Exception as error:
            self.artifact_store.save_failure(
                IngestionFailure(error_type=type(error).__name__, message=str(error))
            )
            raise

    def _run(self) -> CodeIndexResult:
        code = self.config.input.code
        if code is None or not code.enabled:
            raise ValueError("Code indexing requires enabled input.code configuration")
        if not self.config.models.embedding.enabled:
            raise ValueError("Code indexing requires enabled model embedding configuration")

        graph = self._extract_code(code)
        chunks = self._chunk_code(graph)
        self.artifact_store.save_code_documents(graph.source_documents)
        self.artifact_store.save_code_chunks(chunks)
        self._publish_graph(graph)
        vectors = self._embed_and_store(chunks)
        result = CodeIndexResult(
            run_id=self.run_id,
            revision=code.revision,
            code_files=sum(record.kind == "CodeFile" for record in graph.nodes),
            code_symbols=sum(record.kind == "CodeSymbol" for record in graph.nodes),
            chunks=len(chunks),
            vectors=len(vectors),
            vector_dimensions=len(vectors[0].vector) if vectors else 0,
            relationships=len(graph.relationships),
            completed_at=datetime.now(UTC),
        )
        self.artifact_store.save_code_index(result, graph, chunks)
        return result

    @record_stage("code_graph_extraction")
    def _extract_code(self, code: CodeInputConfig) -> CodeGraph:
        return self.code_extractor.extract(code)

    @record_stage("code_chunking")
    def _chunk_code(self, graph: CodeGraph) -> tuple[Chunk, ...]:
        return tuple(
            chunk
            for document in graph.source_documents
            for chunk in self.code_chunking_strategy.chunk(
                document, max_chars=self.config.chunking.max_chunk_chars
            )
        )

    @record_stage("code_graph_storage")
    def _publish_graph(self, graph: CodeGraph) -> None:
        if self.config.storage.graph.publish_code:
            self.graph_store.replace_code_graph(graph.nodes, graph.relationships)

    @record_stage("code_embedding_and_vector_storage")
    def _embed_and_store(self, chunks: tuple[Chunk, ...]) -> tuple[VectorRecord, ...]:
        if not chunks:
            if self.config.storage.vector.publish_chunks:
                self.vector_store.replace_code_records(())
            return ()
        embeddings = self.embedding_provider.embed(tuple(chunk.content for chunk in chunks))
        vectors = tuple(
            VectorRecord(
                id=chunk.id,
                vector=vector,
                content=chunk.content,
                metadata=chunk.metadata,
            )
            for chunk, vector in zip(chunks, embeddings, strict=True)
        )
        if self.config.storage.vector.publish_chunks:
            self.vector_store.replace_code_records(vectors)
        return vectors
