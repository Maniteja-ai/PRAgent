"""Explicit trusted plugins. Configuration selects names; it never imports Python code."""

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .errors import ConfigurationError
from .interfaces import (
    ArtifactStore,
    Chunker,
    DocumentParser,
    EmbeddingProvider,
    GraphStore,
    RequirementExtractor,
    SourceLoader,
    VectorStore,
)

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, stage: str):
        self.stage = stage
        self._factories: dict[str, Callable[[], T]] = {}
        self._instances: dict[str, T] = {}
        self._resources = ExitStack()
        self._closed = False
        self._resource_ids: set[int] = set()

    def register(self, name: str, implementation: T) -> None:
        self.register_factory(name, lambda: implementation)

    def register_factory(self, name: str, factory: Callable[[], T]) -> None:
        if self._closed:
            raise RuntimeError("This registry is closed; create a new pipeline")
        if name in self._factories:
            raise ConfigurationError(f"{self.stage} '{name}' is already registered")
        self._factories[name] = factory

    def require(self, name: str) -> None:
        if self._closed:
            raise RuntimeError("This registry is closed; create a new pipeline")
        if name not in self._factories:
            raise ConfigurationError(f"Unknown {self.stage} '{name}'. Available: {', '.join(self.names())}")

    def resolve(self, name: str) -> T:
        self.require(name)
        if name not in self._instances:
            instance = self._factories[name]()
            self._instances[name] = instance
            close = getattr(instance, "close", None)
            if callable(close) and id(instance) not in self._resource_ids:
                self._resource_ids.add(id(instance))
                self._resources.callback(close)
        return self._instances[name]

    def names(self) -> list[str]:
        return sorted(self._factories)

    def close(self) -> None:
        try:
            self._resources.close()
        finally:
            self._closed = True
            self._instances.clear()


@dataclass
class Components:
    loaders: Registry[SourceLoader] = field(default_factory=lambda: Registry("loader"))
    parsers: Registry[DocumentParser] = field(default_factory=lambda: Registry("parser"))
    chunkers: Registry[Chunker] = field(default_factory=lambda: Registry("chunker"))
    extractors: Registry[RequirementExtractor] = field(default_factory=lambda: Registry("extractor"))
    embeddings: Registry[EmbeddingProvider] = field(default_factory=lambda: Registry("embedding provider"))
    graphs: Registry[GraphStore] = field(default_factory=lambda: Registry("graph store"))
    vectors: Registry[VectorStore] = field(default_factory=lambda: Registry("vector store"))
    artifacts: Registry[ArtifactStore] = field(default_factory=lambda: Registry("artifact store"))

    def describe(self) -> dict[str, list[str]]:
        return {name: registry.names() for name, registry in vars(self).items()}

    def close(self) -> None:
        with ExitStack() as cleanup:
            for registry in vars(self).values():
                cleanup.callback(registry.close)
