"""Register trusted beans, select configured implementations, and inject the pipeline."""

from typing import cast

from ingestion.beans.container import BeanContainer
from ingestion.chunking_strategy.code_interface import CodeChunkingStrategy
from ingestion.chunking_strategy.implementation.fixed_size_chunker import FixedSizeChunker
from ingestion.chunking_strategy.implementation.section_chunker import SectionChunker
from ingestion.chunking_strategy.implementation.section_code_chunker import SectionCodeChunker
from ingestion.chunking_strategy.implementation.syntax_aware_code_chunker import SyntaxAwareCodeChunker
from ingestion.chunking_strategy.interface import ChunkingStrategy
from ingestion.config_loader.models import ApplicationConfig
from ingestion.embedding.implementation.deterministic_embedding import DeterministicEmbeddingProvider
from ingestion.embedding.implementation.fastembed_embedding import FastEmbedEmbeddingProvider
from ingestion.embedding.implementation.gemini_embedding import GeminiEmbeddingProvider
from ingestion.embedding.interface import EmbeddingProvider
from ingestion.evaluation.implementation.recorders import JsonlStageRecorder, NullStageRecorder
from ingestion.evaluation.interface import StageRecorder
from ingestion.extractor.code.implementation.git_code_extractor import GitCodeExtractor
from ingestion.extractor.code.implementation.typescript_code_extractor import TypeScriptCodeExtractor
from ingestion.extractor.code.interface import CodeExtractor
from ingestion.extractor.documents.implementation.github_file_extractor import GitHubFileExtractor
from ingestion.extractor.documents.implementation.local_file_extractor import LocalFileExtractor
from ingestion.extractor.documents.implementation.web_extractor import WebExtractor
from ingestion.extractor.documents.interface import DocumentExtractor
from ingestion.extractor.documents.registry import DocumentExtractorRegistry
from ingestion.extractor.ui.implementation.browser_ui_extractor import BrowserUiExtractor
from ingestion.extractor.ui.implementation.recorded_ui_extractor import RecordedUiExtractor
from ingestion.extractor.ui.interface import UiExtractor
from ingestion.mapping.implementation.disabled_mapping import DisabledMappingResolver
from ingestion.mapping.implementation.evidence_mapping import EvidenceMappingResolver
from ingestion.mapping.implementation.nextjs_route_mapping import NextJsRouteMappingResolver
from ingestion.mapping.implementation.static_dependency_ui_tagger import StaticDependencyUiTagger
from ingestion.mapping.interface import MappingResolver
from ingestion.mapping.ui_candidate_tagger import UiCandidateTagger
from ingestion.pipeline.code_index import CodeIndexPipeline
from ingestion.pipeline.code_ui_refresh import CodeUiRefreshPipeline
from ingestion.pipeline.ingestion_pipeline import IngestionPipeline
from ingestion.requirements.implementation.disabled_extractor import DisabledRequirementExtractor
from ingestion.requirements.implementation.gemini_extractor import GeminiRequirementExtractor
from ingestion.requirements.implementation.rule_based_extractor import RuleBasedRequirementExtractor
from ingestion.requirements.interface import RequirementExtractor
from ingestion.storage.artifacts.implementation.local_artifact_store import LocalArtifactStore
from ingestion.storage.artifacts.interface import ArtifactStore
from ingestion.storage.implementation.in_memory import InMemoryGraphStore, InMemoryVectorStore
from ingestion.storage.implementation.neo4j_graph_store import Neo4jGraphStore
from ingestion.storage.implementation.qdrant_vector_store import QdrantVectorStore
from ingestion.storage.interface import GraphStore, VectorStore

TRUSTED_COMPONENTS = (
    SectionChunker,
    SectionCodeChunker,
    SyntaxAwareCodeChunker,
    FixedSizeChunker,
    LocalFileExtractor,
    WebExtractor,
    GitHubFileExtractor,
    GitCodeExtractor,
    TypeScriptCodeExtractor,
    RecordedUiExtractor,
    BrowserUiExtractor,
    DeterministicEmbeddingProvider,
    FastEmbedEmbeddingProvider,
    GeminiEmbeddingProvider,
    InMemoryVectorStore,
    InMemoryGraphStore,
    Neo4jGraphStore,
    QdrantVectorStore,
    EvidenceMappingResolver,
    DisabledMappingResolver,
    NextJsRouteMappingResolver,
    StaticDependencyUiTagger,
    LocalArtifactStore,
    DisabledRequirementExtractor,
    GeminiRequirementExtractor,
    RuleBasedRequirementExtractor,
    NullStageRecorder,
    JsonlStageRecorder,
)


def create_pipeline(config: ApplicationConfig) -> IngestionPipeline:
    container = _configured_container(config)

    loaders = {source.loader for source in config.input.documents.sources}
    document_extractors = {
        loader: cast(DocumentExtractor, container.select(DocumentExtractor, loader))
        for loader in sorted(loaders)
    }
    container.register_instance(DocumentExtractorRegistry(document_extractors))

    container.bind_selected(ChunkingStrategy, config.chunking.provider)
    container.bind_selected(CodeChunkingStrategy, config.chunking.code_provider)
    container.bind_selected(EmbeddingProvider, config.models.embedding.implementation)
    code_provider = config.input.code.analyzer.provider if config.input.code else "git"
    ui_provider = config.input.ui.explorer.provider if config.input.ui else "recorded"
    mapping_provider = config.input.ui.mapping_resolver.provider if config.input.ui else "none"
    container.bind_selected(CodeExtractor, code_provider)
    container.bind_selected(UiExtractor, ui_provider)
    container.bind_selected(VectorStore, config.storage.vector.provider)
    container.bind_selected(GraphStore, config.storage.graph.provider)
    container.bind_selected(MappingResolver, mapping_provider)
    container.bind_selected(ArtifactStore, config.storage.artifacts.provider)
    container.bind_selected(RequirementExtractor, config.models.requirement_extraction.implementation)
    recorder_name = (
        config.evaluation.recording.provider if config.evaluation.recording.enabled else "none"
    )
    container.bind_selected(StageRecorder, recorder_name)
    return container.create(IngestionPipeline)


def create_code_ui_refresh(config: ApplicationConfig) -> CodeUiRefreshPipeline:
    """Create only the static-code and browser stages; no LLM, embeddings, or Qdrant."""
    container = _configured_container(config)
    code_provider = config.input.code.analyzer.provider if config.input.code else "git"
    ui_provider = config.input.ui.explorer.provider if config.input.ui else "recorded"
    mapping_provider = config.input.ui.mapping_resolver.provider if config.input.ui else "none"
    container.bind_selected(CodeExtractor, code_provider)
    container.bind_selected(UiExtractor, ui_provider)
    container.bind_selected(GraphStore, config.storage.graph.provider)
    container.bind_selected(MappingResolver, mapping_provider)
    container.bind_selected(ArtifactStore, config.storage.artifacts.provider)
    return container.create(CodeUiRefreshPipeline)


def create_code_index(config: ApplicationConfig) -> CodeIndexPipeline:
    """Create only code extraction, chunking, embeddings, and graph/vector storage."""
    container = _configured_container(config)
    code_provider = config.input.code.analyzer.provider if config.input.code else "git"
    recorder_name = (
        config.evaluation.recording.provider if config.evaluation.recording.enabled else "none"
    )
    container.bind_selected(CodeExtractor, code_provider)
    container.bind_selected(ChunkingStrategy, config.chunking.provider)
    container.bind_selected(CodeChunkingStrategy, config.chunking.code_provider)
    container.bind_selected(EmbeddingProvider, config.models.embedding.implementation)
    container.bind_selected(VectorStore, config.storage.vector.provider)
    container.bind_selected(GraphStore, config.storage.graph.provider)
    container.bind_selected(ArtifactStore, config.storage.artifacts.provider)
    container.bind_selected(StageRecorder, recorder_name)
    return container.create(CodeIndexPipeline)


def _configured_container(config: ApplicationConfig) -> BeanContainer:
    container = BeanContainer()
    container.register_components(TRUSTED_COMPONENTS)
    container.register_instance(config)
    container.bind_selected(UiCandidateTagger, "static_dependencies")
    for bean in (
        config.project,
        config.input,
        config.models,
        config.chunking,
        config.storage,
        config.constraints,
        config.evaluation,
    ):
        container.register_instance(bean)
    return container
