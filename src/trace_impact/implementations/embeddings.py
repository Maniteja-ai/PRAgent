"""LangChain embedding adapter. No storage decisions belong here."""

from collections.abc import Callable

from ..models import EmbeddingProfile


class LangChainEmbeddingProvider:
    def __init__(self, model, profile: EmbeddingProfile, close: Callable[[], None] | None = None):
        self.model, self.profile = model, profile
        self._close = close

    def close(self) -> None:
        if self._close is not None:
            self._close()
            self._close = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        from langsmith import tracing_context

        with tracing_context(enabled=False):
            return self.model.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        from langsmith import tracing_context

        with tracing_context(enabled=False):
            return self.model.embed_query(text)
