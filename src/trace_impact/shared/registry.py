"""Explicit trusted plugins. Configuration selects names; it never imports Python code."""

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Generic, TypeVar

from pydantic import BaseModel

from trace_impact.ingestion.code.interfaces import CodeAnalyzer
from trace_impact.ingestion.interfaces import (
    ArtifactStore,
    Chunker,
    DocumentParser,
    EmbeddingProvider,
    GraphStore,
    RequirementExtractor,
    SourceLoader,
    VectorStore,
)
from trace_impact.retrieval.interfaces import EvidenceSelector, Reranker
from trace_impact.shared.component_config import ComponentDefinition
from trace_impact.shared.errors import ConfigurationError
from trace_impact.shared.names import normalize_component_name

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, stage: str):
        self.stage = stage
        self._factories: dict[str, Callable[[], T]] = {}
        self._configured_factories: dict[str, Callable[[BaseModel], T]] = {}
        self._instances: dict[str | tuple[str, str], T] = {}
        self._resources = ExitStack()
        self._closed = False
        self._resource_ids: set[int] = set()
        self._definitions: dict[str, ComponentDefinition] = {}

    def register(
        self, name: str, implementation: T, *, definition: ComponentDefinition | None = None
    ) -> None:
        self.register_factory(name, lambda: implementation, definition=definition)

    def register_factory(
        self, name: str, factory: Callable[[], T], *, definition: ComponentDefinition | None = None
    ) -> None:
        if self._closed:
            raise RuntimeError("This registry is closed; create a new pipeline")
        name = normalize_component_name(name)
        if name in self._factories:
            raise ConfigurationError(f"{self.stage} '{name}' is already registered")
        self._factories[name] = factory
        if definition is not None:
            self._definitions[name] = definition

    def register_configured_factory(
        self, name: str, factory: Callable[[BaseModel], T], *, definition: ComponentDefinition | None = None
    ) -> None:
        """Add JSON-configured construction; a legacy zero-argument factory may coexist."""
        if self._closed:
            raise RuntimeError("This registry is closed; create a new pipeline")
        name = normalize_component_name(name)
        if name in self._configured_factories:
            raise ConfigurationError(f"Configured {self.stage} '{name}' is already registered")
        self._configured_factories[name] = factory
        if definition is not None:
            self._definitions[name] = definition

    def define(self, name: str, definition: ComponentDefinition) -> None:
        name = normalize_component_name(name)
        if name not in self.names():
            raise ConfigurationError("Register the component before defining its configuration")
        self._definitions[name] = definition

    def definitions(self) -> dict:
        return {
            name: self._definitions.get(
                name,
                ComponentDefinition(name, "Registered custom implementation; see its plugin documentation."),
            ).schema()
            for name in self.names()
        }

    def validate_options(self, name: str, options: dict) -> None:
        definition = self._definitions.get(normalize_component_name(name))
        if definition and definition.options_model:
            definition.options_model.model_validate(options)

    def require(self, selection: str | BaseModel) -> None:
        if self._closed:
            raise RuntimeError("This registry is closed; create a new pipeline")
        name = normalize_component_name(selection if isinstance(selection, str) else selection.provider)
        factories = self._factories if isinstance(selection, str) else self._configured_factories
        if name not in factories:
            matches = get_close_matches(name, factories, n=1, cutoff=0.6)
            hint = f" Did you mean '{matches[0]}'?" if matches else ""
            raise ConfigurationError(
                f"Unknown {self.stage} '{name}' for this configuration form. "
                f"Available: {', '.join(sorted(factories))}.{hint}"
            )

    def resolve(self, selection: str | BaseModel) -> T:
        self.require(selection)
        selection = (
            normalize_component_name(selection)
            if isinstance(selection, str)
            else selection.model_copy(update={"provider": normalize_component_name(selection.provider)})
        )
        key = selection if isinstance(selection, str) else (selection.provider, selection.model_dump_json())
        if key not in self._instances:
            instance = (
                self._factories[selection]()
                if isinstance(selection, str)
                else self._configured_factories[selection.provider](selection)
            )
            self._instances[key] = instance
            close = getattr(instance, "close", None)
            if callable(close) and id(instance) not in self._resource_ids:
                self._resource_ids.add(id(instance))
                self._resources.callback(close)
        return self._instances[key]

    def names(self, *, configured: bool | None = None) -> list[str]:
        if configured is not None:
            return sorted(self._configured_factories if configured else self._factories)
        return sorted(self._factories.keys() | self._configured_factories.keys())

    def close(self) -> None:
        try:
            self._resources.close()
        finally:
            self._closed = True
            self._instances.clear()


@dataclass
class Components:
    code_analyzers: Registry[CodeAnalyzer] = field(default_factory=lambda: Registry("code analyzer"))
    loaders: Registry[SourceLoader] = field(default_factory=lambda: Registry("loader"))
    parsers: Registry[DocumentParser] = field(default_factory=lambda: Registry("parser"))
    chunkers: Registry[Chunker] = field(default_factory=lambda: Registry("chunker"))
    extractors: Registry[RequirementExtractor] = field(default_factory=lambda: Registry("extractor"))
    embeddings: Registry[EmbeddingProvider] = field(default_factory=lambda: Registry("embedding provider"))
    graphs: Registry[GraphStore] = field(default_factory=lambda: Registry("graph store"))
    vectors: Registry[VectorStore] = field(default_factory=lambda: Registry("vector store"))
    artifacts: Registry[ArtifactStore] = field(default_factory=lambda: Registry("artifact store"))
    rerankers: Registry[Reranker] = field(default_factory=lambda: Registry("reranker"))
    selectors: Registry[EvidenceSelector] = field(default_factory=lambda: Registry("evidence selector"))

    def describe(self) -> dict[str, list[str]]:
        return {name: registry.names() for name, registry in vars(self).items()}

    def close(self) -> None:
        with ExitStack() as cleanup:
            for registry in vars(self).values():
                cleanup.callback(registry.close)
