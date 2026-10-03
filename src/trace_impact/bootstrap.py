"""Register built-ins. Factories are lazy: collection does not need model/database credentials."""

import logging
from contextlib import ExitStack

import httpx

from trace_impact.ingestion.config import EmbeddingConfig, ExtractionConfig
from trace_impact.ingestion.documents.chunkers import SectionChunker
from trace_impact.ingestion.documents.loaders import GitHubFileLoader, LocalFileLoader, WebLoader
from trace_impact.ingestion.documents.parsers import HtmlParser, MarkdownParser
from trace_impact.ingestion.models import EmbeddingProfile, Extraction
from trace_impact.ingestion.storage.artifact_store import FileArtifactRepository
from trace_impact.shared.component_config import ComponentDefinition, EmptyOptions, GitHubOptions
from trace_impact.shared.errors import ConfigurationError
from trace_impact.shared.events import JsonEventSink
from trace_impact.shared.rate_limit import RequestPacer
from trace_impact.shared.registry import Components
from trace_impact.shared.settings import Settings


def default_components(settings: Settings) -> Components:
    components = Components()
    from trace_impact.ingestion.code.config import TypeScriptOptions
    from trace_impact.ingestion.code.typescript_analyzer import TypeScriptAnalyzer

    components.code_analyzers.register_configured_factory(
        "typescript",
        lambda config: TypeScriptAnalyzer(TypeScriptOptions.model_validate(config.options)),
        definition=ComponentDefinition(
            "TypeScript compiler",
            "Analyze immutable Git sources without executing the repository.",
            TypeScriptOptions,
        ),
    )
    components.loaders.register_factory("web", lambda: WebLoader(timeout_seconds=settings.request_timeout))
    components.loaders.register_factory(
        "github_file",
        lambda: GitHubFileLoader(WebLoader(timeout_seconds=settings.request_timeout)),
    )
    components.loaders.register_factory("local_file", LocalFileLoader)
    components.parsers.register_factory("html", HtmlParser)
    components.parsers.register_factory("markdown", MarkdownParser)
    components.chunkers.register_factory("section", SectionChunker)
    components.artifacts.register_factory("local", FileArtifactRepository)
    components.loaders.define(
        "web",
        ComponentDefinition("Website", "Read one public HTTPS documentation page.", EmptyOptions),
    )
    components.loaders.define(
        "github_file",
        ComponentDefinition(
            "GitHub file", "Read a public repository file at a pinned commit.", GitHubOptions
        ),
    )
    components.loaders.define(
        "local_file",
        ComponentDefinition(
            "Local file", "Read a file inside this project's configuration directory.", EmptyOptions
        ),
    )
    components.parsers.define("html", ComponentDefinition("HTML", "Extract documentation text from HTML."))
    components.parsers.define(
        "markdown", ComponentDefinition("Markdown", "Parse Markdown documents such as README.md.")
    )

    def extractor(config: ExtractionConfig | None = None):
        selected = (
            settings
            if config is None
            else settings.model_copy(
                update={
                    "ingestion_model": config.model,
                    "max_output_tokens": config.max_output_tokens,
                }
            )
        )
        selected.require_model()
        if config and config.thinking_level is not None:
            raise ConfigurationError("thinking_level is supported by the Gemini adapter only")
        from langchain_openai import ChatOpenAI
        from openai import OpenAIError

        from trace_impact.ingestion.requirements.langchain_extractor import LangChainRequirementExtractor

        with ExitStack() as resources:
            client = resources.enter_context(httpx.Client(timeout=settings.request_timeout))
            llm = ChatOpenAI(
                model=selected.ingestion_model,
                api_key=settings.openai_api_key,
                timeout=settings.request_timeout,
                max_retries=settings.model_retries,
                http_client=client,
                max_tokens=selected.max_output_tokens,
                use_responses_api=True,
                store=False,
            )
            return LangChainRequirementExtractor(
                llm.with_structured_output(Extraction, method="json_schema", strict=True, include_raw=True),
                provider="openai",
                model=selected.ingestion_model,
                configuration_id=f"responses-json-schema-v1:tokens={selected.max_output_tokens}",
                events=JsonEventSink(logging.getLogger("trace_impact.events")),
                request_errors=(OpenAIError, httpx.HTTPError),
                before_request=RequestPacer(config.requests_per_minute if config else 0).wait,
                close=resources.pop_all().close,
            )

    def embeddings(config: EmbeddingConfig | None = None):
        selected = (
            settings
            if config is None
            else settings.model_copy(
                update={
                    "embedding_model": config.model,
                    "embedding_dimensions": config.dimensions,
                }
            )
        )
        selected.require_embeddings()
        from langchain_openai import OpenAIEmbeddings

        from trace_impact.ingestion.embeddings.langchain_embeddings import LangChainEmbeddingProvider

        with ExitStack() as resources:
            client = resources.enter_context(httpx.Client(timeout=settings.request_timeout))
            model = OpenAIEmbeddings(
                model=selected.embedding_model,
                api_key=settings.openai_api_key,
                dimensions=selected.embedding_dimensions,
                request_timeout=settings.request_timeout,
                max_retries=settings.model_retries,
                http_client=client,
                check_embedding_ctx_length=False,
            )
            return LangChainEmbeddingProvider(
                model,
                EmbeddingProfile(
                    provider="openai",
                    model=selected.embedding_model,
                    dimensions=selected.embedding_dimensions,
                ),
                close=resources.pop_all().close,
                before_request=RequestPacer(config.requests_per_minute if config else 0).wait,
            )

    def graph():
        settings.require_database()
        from trace_impact.ingestion.storage.neo4j_requirement_store import Neo4jRequirementStore

        return Neo4jRequirementStore(
            settings.neo4j_uri,
            settings.neo4j_username,
            settings.neo4j_password.get_secret_value(),
            settings.neo4j_database,
        )

    def vector():
        from qdrant_client import QdrantClient

        from trace_impact.ingestion.storage.qdrant_store import QdrantVectorStore

        client = (
            QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key.get_secret_value() or None,
                timeout=30,
            )
            if settings.qdrant_url
            else QdrantClient(path=settings.qdrant_path)
        )
        return QdrantVectorStore(client)

    def gemini_extractor(config: ExtractionConfig):
        from trace_impact.ingestion.requirements.gemini_extractor import build_extractor

        return build_extractor(settings, config)

    def gemini_embeddings(config: EmbeddingConfig):
        from trace_impact.ingestion.embeddings.gemini_embeddings import build_embeddings

        return build_embeddings(settings, config)

    def gemini_interactions_extractor(config: ExtractionConfig):
        from trace_impact.ingestion.requirements.gemini_interactions_extractor import build_extractor

        return build_extractor(settings, config)

    components.extractors.register_factory("langchain", extractor)
    components.extractors.register_factory("openai", extractor)
    components.extractors.register_configured_factory("openai", extractor)
    components.extractors.register_configured_factory("gemini", gemini_extractor)
    components.extractors.register_configured_factory("gemini_interactions", gemini_interactions_extractor)
    components.embeddings.register_configured_factory("openai", embeddings)
    components.embeddings.register_configured_factory("gemini", gemini_embeddings)
    components.embeddings.register_factory("openai", embeddings)
    for registry in (components.extractors, components.embeddings):
        for name in registry.names(configured=True):
            registry.define(
                name,
                ComponentDefinition(
                    name.replace("_", " ").title(),
                    "Model ID and pacing are configured below. Credentials are read from .env.",
                    EmptyOptions,
                ),
            )
    components.graphs.register_factory("neo4j", graph)
    components.vectors.register_factory("qdrant", vector)
    from trace_impact.retrieval.config import (
        CompatibleLLMOptions,
        CrossEncoderOptions,
        GeminiRerankerOptions,
        ThresholdOptions,
        TopKOptions,
    )
    from trace_impact.retrieval.rerankers.identity import IdentityReranker
    from trace_impact.retrieval.selectors import ScoreThresholdSelector, TopKSelector

    def compatible_reranker(config):
        from trace_impact.retrieval.rerankers.compatible import build_compatible_reranker

        return build_compatible_reranker(CompatibleLLMOptions.model_validate(config.options))

    components.rerankers.register_configured_factory(
        "openai_compatible",
        compatible_reranker,
        definition=ComponentDefinition(
            "Compatible LLM API",
            "Explicit Chat Completions endpoint, model and API-key environment name.",
            CompatibleLLMOptions,
        ),
    )

    def cross_encoder(config):
        from trace_impact.retrieval.rerankers.cross_encoder import build_cross_encoder

        return build_cross_encoder(CrossEncoderOptions.model_validate(config.options))

    components.rerankers.register_configured_factory(
        "cross_encoder",
        cross_encoder,
        definition=ComponentDefinition(
            "Local cross-encoder", "Score query-passage pairs with a pinned CPU model.", CrossEncoderOptions
        ),
    )

    def reranker(config):
        from trace_impact.retrieval.rerankers.gemini import build_gemini_reranker

        return build_gemini_reranker(settings, GeminiRerankerOptions.model_validate(config.options))

    components.rerankers.register_configured_factory(
        "identity",
        lambda config: IdentityReranker(),
        definition=ComponentDefinition(
            "Original retrieval scores", "Baseline without model reranking.", EmptyOptions
        ),
    )
    components.rerankers.register_configured_factory(
        "gemini",
        reranker,
        definition=ComponentDefinition(
            "Gemini relevance grades",
            "LangChain structured reranking of existing passages.",
            GeminiRerankerOptions,
        ),
    )
    components.selectors.register_configured_factory(
        "top_k",
        lambda config: TopKSelector(TopKOptions.model_validate(config.options)),
        definition=ComponentDefinition(
            "Fixed result count", "Baseline ranked truncation without a relevance threshold.", TopKOptions
        ),
    )
    components.selectors.register_configured_factory(
        "score_threshold",
        lambda config: ScoreThresholdSelector(ThresholdOptions.model_validate(config.options)),
        definition=ComponentDefinition(
            "Relevance selection",
            "Apply a named score scale, threshold, deduplication and result cap.",
            ThresholdOptions,
        ),
    )
    return components
