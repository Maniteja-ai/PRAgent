"""Composition root: the only place that chooses and wires concrete implementations."""

import logging
from contextlib import ExitStack

import httpx

from .application.services import CollectionService, ExtractionService, GraphPublicationService
from .domain.models import Extraction
from .domain.policies import GroundingPolicy
from .infrastructure.artifacts import FileArtifactRepository
from .infrastructure.events import JsonEventSink
from .infrastructure.langchain_extractor import LangChainRequirementExtractor
from .infrastructure.neo4j_store import Neo4jStore
from .infrastructure.sources import (
    HttpSourceReader,
    LocalFileReader,
    SnapshotDocumentProcessor,
    SourceReaderRegistry,
)
from .settings import Settings


class ApplicationContainer:
    """One CLI invocation owns its clients; context exit closes them even after failures."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.resources = ExitStack()
        self.artifacts = FileArtifactRepository()
        self.events = JsonEventSink(logging.getLogger("trace_impact.events"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.resources.__exit__(*args)

    def collection(self) -> CollectionService:
        client = self.resources.enter_context(
            httpx.Client(
                timeout=30,
                follow_redirects=False,
                headers={"User-Agent": "TraceImpact/0.2 documentation-ingestion"},
            )
        )
        readers = SourceReaderRegistry({"": LocalFileReader(), "https": HttpSourceReader(client)})
        return CollectionService(SnapshotDocumentProcessor(readers), self.artifacts, self.events)

    def extraction(self, model: str | None = None) -> ExtractionService:
        settings = self.settings.model_copy(update={"ingestion_model": model}) if model else self.settings
        settings.require_model()
        from langchain_openai import ChatOpenAI

        http_client = self.resources.enter_context(httpx.Client(timeout=settings.request_timeout))
        llm = ChatOpenAI(
            model=settings.ingestion_model,
            api_key=settings.openai_api_key,
            timeout=settings.request_timeout,
            max_retries=settings.model_retries,
            max_tokens=settings.max_output_tokens,
            use_responses_api=True,
            http_client=http_client,
            store=False,
        )
        structured = llm.with_structured_output(
            Extraction, method="json_schema", strict=True, include_raw=True
        )
        adapter = LangChainRequirementExtractor(
            structured,
            provider="openai",
            model=settings.ingestion_model,
            configuration_id=f"responses-json-schema-v1:tokens={settings.max_output_tokens}",
            events=self.events,
        )
        return ExtractionService(adapter, self.artifacts, GroundingPolicy(), self.events)

    def database(self) -> Neo4jStore:
        self.settings.require_database()
        store = Neo4jStore(
            self.settings.neo4j_uri,
            self.settings.neo4j_username,
            self.settings.neo4j_password.get_secret_value(),
            self.settings.neo4j_database,
        )
        self.resources.callback(store.close)
        return store

    def publication(self, graph: Neo4jStore) -> GraphPublicationService:
        return GraphPublicationService(self.artifacts, graph, self.events)
