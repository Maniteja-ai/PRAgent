from ingestion.chunking_strategy.code_interface import CodeChunkingStrategy
from ingestion.chunking_strategy.interface import ChunkingStrategy
from ingestion.config_loader.models import ApplicationConfig
from ingestion.domain.models import (
    Chunk,
    CodeGraph,
    ConfirmedMapping,
    GraphRecord,
    GraphRelationship,
    IngestionFailure,
    IngestionResult,
    RawDocument,
    Requirement,
    UiObservation,
    VectorRecord,
)
from ingestion.embedding.interface import EmbeddingProvider
from ingestion.evaluation.decorators import record_stage
from ingestion.evaluation.interface import StageRecorder
from ingestion.extractor.code.interface import CodeExtractor
from ingestion.extractor.documents.registry import DocumentExtractorRegistry
from ingestion.extractor.ui.interface import UiExtractor
from ingestion.mapping.interface import MappingResolver
from ingestion.mapping.route_observation import observed_route_relationships
from ingestion.requirements.interface import RequirementExtractor
from ingestion.storage.artifacts.interface import ArtifactStore
from ingestion.storage.interface import GraphStore, VectorStore


class IngestionPipeline:
    def __init__(
        self,
        config: ApplicationConfig,
        document_extractors: DocumentExtractorRegistry,
        chunking_strategy: ChunkingStrategy,
        embedding_provider: EmbeddingProvider,
        code_extractor: CodeExtractor,
        ui_extractor: UiExtractor,
        vector_store: VectorStore,
        graph_store: GraphStore,
        mapping_resolver: MappingResolver,
        artifact_store: ArtifactStore,
        requirement_extractor: RequirementExtractor,
        stage_recorder: StageRecorder,
        code_chunking_strategy: CodeChunkingStrategy,
    ) -> None:
        self.config = config
        self.document_extractors = document_extractors
        self.chunking_strategy = chunking_strategy
        self.embedding_provider = embedding_provider
        self.code_extractor = code_extractor
        self.ui_extractor = ui_extractor
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.mapping_resolver = mapping_resolver
        self.artifact_store = artifact_store
        self.requirement_extractor = requirement_extractor
        self.stage_recorder = stage_recorder
        self.code_chunking_strategy = code_chunking_strategy
        self.run_id = f"{config.project.id}:{config.input.repository.baseline_commit}"

    def __enter__(self) -> "IngestionPipeline":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.requirement_extractor.close()
        self.embedding_provider.close()
        self.vector_store.close()
        self.graph_store.close()

    def run(self) -> IngestionResult:
        self.artifact_store.save_config(self.config)
        try:
            return self._run()
        except Exception as exc:
            self.artifact_store.save_failure(
                IngestionFailure(error_type=type(exc).__name__, message=str(exc))
            )
            raise

    def resume_after_embedding(self) -> IngestionResult:
        """Continue from saved documents, chunks, and vectors without embedding again."""
        try:
            self.artifact_store.archive_failure()
            self.artifact_store.save_config(self.config)
            return self._resume_after_embedding()
        except Exception as exc:
            self.artifact_store.save_failure(
                IngestionFailure(error_type=type(exc).__name__, message=str(exc))
            )
            raise

    @record_stage("resume_after_embedding")
    def _resume_after_embedding(self) -> IngestionResult:
        documents = self.artifact_store.load_documents()
        chunks = self.artifact_store.load_chunks()
        code_chunks = self.artifact_store.load_code_chunks()
        vector_count = self.artifact_store.load_vector_count()
        expected_vectors = len(chunks) + len(code_chunks) if self.config.models.embedding.enabled else 0
        if vector_count != expected_vectors:
            raise ValueError(
                "Saved vector checkpoint does not match the chunk checkpoint; "
                "rerun ingestion from the beginning"
            )

        code_graph = self._extract_code_graph()
        self._publish_code_graph(code_graph)
        requirements = self._extract_requirements(chunks)
        graph_records = self._publish_graph(code_graph, requirements)
        observations = self._explore_ui()
        confirmed_mappings = self._resolve_mappings(
            code_graph.nodes, observations, code_graph.relationships
        )
        return self._finalize(
            documents,
            chunks + code_chunks,
            (),
            graph_records,
            code_graph.relationships,
            observations,
            confirmed_mappings,
            requirements,
            stored_vector_count=vector_count,
            code_files=sum(record.kind == "CodeFile" for record in code_graph.nodes),
            code_chunks=len(code_chunks),
        )

    @record_stage("ingestion_pipeline")
    def _run(self) -> IngestionResult:
        documents = self._extract_documents()
        chunks = self._chunk_documents(documents)
        code_graph = self._extract_code_graph()
        code_chunks = self._chunk_code_documents(code_graph.source_documents)
        vectors = self._create_and_store_vectors(chunks + code_chunks)
        self._publish_code_graph(code_graph)
        requirements = self._extract_requirements(chunks)
        graph_records = self._publish_graph(code_graph, requirements)
        observations = self._explore_ui()
        confirmed_mappings = self._resolve_mappings(
            code_graph.nodes, observations, code_graph.relationships
        )
        return self._finalize(
            documents,
            chunks + code_chunks,
            vectors,
            graph_records,
            code_graph.relationships,
            observations,
            confirmed_mappings,
            requirements,
            code_files=sum(record.kind == "CodeFile" for record in code_graph.nodes),
            code_chunks=len(code_chunks),
        )

    @record_stage("document_extraction")
    def _extract_documents(self) -> tuple[RawDocument, ...]:
        documents = tuple(
            self.document_extractors.extract(source) for source in self.config.input.documents.sources
        )
        if len(documents) > self.config.constraints.limits.documents:
            raise ValueError("Document limit exceeded")
        self.artifact_store.save_documents(documents)
        return documents

    @record_stage("chunking")
    def _chunk_documents(self, documents: tuple[RawDocument, ...]) -> tuple[Chunk, ...]:
        chunks = tuple(
            chunk
            for document in documents
            for chunk in self.chunking_strategy.chunk(
                document, max_chars=self.config.chunking.max_chunk_chars
            )
        )
        self.artifact_store.save_chunks(chunks)
        return chunks

    @record_stage("code_chunking")
    def _chunk_code_documents(self, documents: tuple[RawDocument, ...]) -> tuple[Chunk, ...]:
        chunks = tuple(
            chunk
            for document in documents
            for chunk in self.code_chunking_strategy.chunk(
                document, max_chars=self.config.chunking.max_chunk_chars
            )
        )
        self.artifact_store.save_code_documents(documents)
        self.artifact_store.save_code_chunks(chunks)
        return chunks

    @record_stage("embedding_and_vector_storage")
    def _create_and_store_vectors(self, chunks: tuple[Chunk, ...]) -> tuple[VectorRecord, ...]:
        vectors: tuple[VectorRecord, ...] = ()
        if self.config.models.embedding.enabled and chunks:
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
            document_vectors = tuple(
                vector for vector in vectors if vector.metadata.get("kind") != "code"
            )
            code_vectors = tuple(
                vector for vector in vectors if vector.metadata.get("kind") == "code"
            )
            if document_vectors:
                self.vector_store.save(document_vectors)
            self.vector_store.replace_code_records(code_vectors)
        elif self.config.models.embedding.enabled:
            self.vector_store.replace_code_records(())
        self.artifact_store.save_vectors(vectors)
        return vectors

    @record_stage("requirement_extraction")
    def _extract_requirements(self, chunks: tuple[Chunk, ...]) -> tuple[Requirement, ...]:
        requirements = self.requirement_extractor.extract(chunks)
        self.artifact_store.save_requirements(requirements)
        return requirements

    @record_stage("code_graph_extraction")
    def _extract_code_graph(self) -> CodeGraph:
        code = self.config.input.code
        if code is None or not code.enabled:
            return CodeGraph()
        return self.code_extractor.extract(code)

    @record_stage("graph_storage")
    def _publish_graph(
        self, code_graph: CodeGraph, requirements: tuple[Requirement, ...]
    ) -> tuple[GraphRecord, ...]:
        requirement_records = tuple(
            GraphRecord(
                id=f"requirement:{item.id}",
                kind="Requirement",
                properties={
                    "statement": item.statement,
                    "source_chunk_id": item.source_chunk_id,
                    "evidence": item.evidence,
                },
            )
            for item in requirements
        )
        graph_records = code_graph.nodes + requirement_records
        self.graph_store.save(requirement_records)
        self.artifact_store.save_graph_records(graph_records)
        self.artifact_store.save_graph_relationships(code_graph.relationships)
        return graph_records

    @record_stage("code_graph_storage")
    def _publish_code_graph(self, code_graph: CodeGraph) -> None:
        if self.config.storage.graph.publish_code:
            self.graph_store.replace_code_graph(code_graph.nodes, code_graph.relationships)

    @record_stage("ui_exploration")
    def _explore_ui(self) -> tuple[UiObservation, ...]:
        observations: tuple[UiObservation, ...] = ()
        ui = self.config.input.ui
        if ui is not None and ui.enabled:
            observations = self.ui_extractor.extract(ui)
            ui_records = tuple(
                GraphRecord(
                    id=item.id,
                    kind="UiPage",
                    properties={
                        "url": item.url,
                        "title": item.page_title,
                        "content_sha256": item.content_sha256,
                        "http_status": str(item.http_status),
                        "captured": str(item.captured).lower(),
                        "content_ready": str(item.content_ready).lower(),
                    },
                )
                for item in observations
            )
            self.graph_store.save(ui_records)
        self.artifact_store.save_ui_observations(observations)
        return observations

    @record_stage("mapping_resolution")
    def _resolve_mappings(
        self,
        code_records: tuple[GraphRecord, ...],
        observations: tuple[UiObservation, ...],
        code_relationships: tuple[GraphRelationship, ...],
    ) -> tuple[ConfirmedMapping, ...]:
        confirmed_mappings = self.mapping_resolver.resolve(
            code_records, observations, code_relationships
        )
        self.graph_store.save_relationships(
            observed_route_relationships(code_records, observations)
        )
        if len(confirmed_mappings) > self.config.constraints.limits.confirmed_mappings:
            raise ValueError("Confirmed mapping limit exceeded")
        if self.config.storage.graph.publish_confirmed_mappings and observations:
            self.graph_store.replace_mappings(
                tuple(observation.id for observation in observations), confirmed_mappings
            )
        self.artifact_store.save_mappings(confirmed_mappings)
        return confirmed_mappings

    @record_stage("finalization")
    def _finalize(
        self,
        documents: tuple[RawDocument, ...],
        chunks: tuple[Chunk, ...],
        vectors: tuple[VectorRecord, ...],
        graph_records: tuple[GraphRecord, ...],
        graph_relationships: tuple[GraphRelationship, ...],
        observations: tuple[UiObservation, ...],
        confirmed_mappings: tuple[ConfirmedMapping, ...],
        requirements: tuple[Requirement, ...],
        *,
        stored_vector_count: int | None = None,
        code_files: int = 0,
        code_chunks: int = 0,
    ) -> IngestionResult:
        result = IngestionResult(
            documents=len(documents),
            chunks=len(chunks),
            vectors=len(vectors) if stored_vector_count is None else stored_vector_count,
            graph_records=len(graph_records),
            graph_relationships=len(graph_relationships),
            ui_observations=len(observations),
            confirmed_mappings=len(confirmed_mappings),
            requirements=len(requirements),
            code_files=code_files,
            code_chunks=code_chunks,
        )
        self.artifact_store.save_result(result)
        return result
