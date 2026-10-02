"""Register built-ins. Factories are lazy: collection does not need model/database credentials."""

import logging
from contextlib import ExitStack

import httpx

from .config import Settings
from .events import JsonEventSink
from .implementations.chunkers import SectionChunker
from .implementations.loaders import GitHubFileLoader, LocalFileLoader, WebLoader
from .implementations.parsers import HtmlParser, MarkdownParser
from .implementations.storage.artifacts import FileArtifactRepository
from .models import EmbeddingProfile, Extraction
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

    def extractor():
        settings.require_model()
        from langchain_openai import ChatOpenAI

        from .implementations.extractors import LangChainRequirementExtractor

        with ExitStack() as resources:
            client = resources.enter_context(httpx.Client(timeout=settings.request_timeout))
            llm = ChatOpenAI(
                model=settings.ingestion_model,
                api_key=settings.openai_api_key,
                timeout=settings.request_timeout,
                max_retries=settings.model_retries,
                http_client=client,
                max_tokens=settings.max_output_tokens,
                use_responses_api=True,
                store=False,
            )
            return LangChainRequirementExtractor(
                llm.with_structured_output(Extraction, method="json_schema", strict=True, include_raw=True),
                provider="openai",
                model=settings.ingestion_model,
                configuration_id=f"responses-json-schema-v1:tokens={settings.max_output_tokens}",
                events=JsonEventSink(logging.getLogger("trace_impact.events")),
                close=resources.pop_all().close,
            )

    def embeddings():
        settings.require_embeddings()
        from langchain_openai import OpenAIEmbeddings

        from .implementations.embeddings import LangChainEmbeddingProvider

        with ExitStack() as resources:
            client = resources.enter_context(httpx.Client(timeout=settings.request_timeout))
            model = OpenAIEmbeddings(
                model=settings.embedding_model,
                api_key=settings.openai_api_key,
                dimensions=settings.embedding_dimensions,
                request_timeout=settings.request_timeout,
                max_retries=settings.model_retries,
                http_client=client,
            )
            return LangChainEmbeddingProvider(
                model,
                EmbeddingProfile(
                    provider="openai",
                    model=settings.embedding_model,
                    dimensions=settings.embedding_dimensions,
                ),
                close=resources.pop_all().close,
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

    components.extractors.register_factory("langchain", extractor)
    components.embeddings.register_factory("openai", embeddings)
    components.graphs.register_factory("neo4j", graph)
    components.vectors.register_factory("qdrant", vector)
    return components
