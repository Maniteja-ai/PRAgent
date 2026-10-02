"""Register built-ins. Factories are lazy: collection does not need model/database credentials."""

import logging
from contextlib import ExitStack

import httpx

from .config import Settings
from .errors import ConfigurationError
from .events import JsonEventSink
from .implementations.chunkers import SectionChunker
from .implementations.loaders import GitHubFileLoader, LocalFileLoader, WebLoader
from .implementations.parsers import HtmlParser, MarkdownParser
from .implementations.rate_limit import RequestPacer
from .implementations.storage.artifacts import FileArtifactRepository
from .models import EmbeddingConfig, EmbeddingProfile, Extraction, ExtractionConfig
from .registry import Components


def default_components(settings: Settings) -> Components:
    components = Components()
    components.loaders.register_factory("web", WebLoader)
    components.loaders.register_factory("github_file", GitHubFileLoader)
    components.loaders.register_factory("local_file", LocalFileLoader)
    components.parsers.register_factory("html", HtmlParser)
    components.parsers.register_factory("markdown", MarkdownParser)
    components.chunkers.register_factory("section", SectionChunker)
    components.artifacts.register_factory("local", FileArtifactRepository)

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

        from .implementations.extractors import LangChainRequirementExtractor

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

        from .implementations.embeddings import LangChainEmbeddingProvider

        with ExitStack() as resources:
            client = resources.enter_context(httpx.Client(timeout=settings.request_timeout))
            model = OpenAIEmbeddings(
                model=selected.embedding_model,
                api_key=settings.openai_api_key,
                dimensions=selected.embedding_dimensions,
                request_timeout=settings.request_timeout,
                max_retries=settings.model_retries,
                http_client=client,
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
        from .implementations.storage.neo4j import Neo4jStore

        return Neo4jStore(
            settings.neo4j_uri,
            settings.neo4j_username,
            settings.neo4j_password.get_secret_value(),
            settings.neo4j_database,
        )

    def vector():
        from qdrant_client import QdrantClient

        from .implementations.storage.qdrant import QdrantVectorStore

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
        from .implementations.gemini import build_extractor

        return build_extractor(settings, config)

    def gemini_embeddings(config: EmbeddingConfig):
        from .implementations.gemini import build_embeddings

        return build_embeddings(settings, config)

    components.extractors.register_factory("langchain", extractor)
    components.extractors.register_factory("openai", extractor)
    components.extractors.register_configured_factory("openai", extractor)
    components.extractors.register_configured_factory("gemini", gemini_extractor)
    components.embeddings.register_configured_factory("openai", embeddings)
    components.embeddings.register_configured_factory("gemini", gemini_embeddings)
    components.embeddings.register_factory("openai", embeddings)
    components.graphs.register_factory("neo4j", graph)
    components.vectors.register_factory("qdrant", vector)
    return components
