"""LangChain embedding adapter. No storage decisions belong here."""

from collections.abc import Callable

from trace_impact.ingestion.models import EmbeddingProfile


class LangChainEmbeddingProvider:
    def __init__(
        self,
        model,
        profile: EmbeddingProfile,
        close: Callable[[], None] | None = None,
        before_request: Callable[[], None] | None = None,
    ):
        self.model, self.profile = model, profile
        self._close = close
        self.before_request = before_request

    def close(self) -> None:
        if self._close is not None:
            self._close()
            self._close = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        from langsmith import tracing_context

        if self.before_request:
            self.before_request()
        with tracing_context(enabled=False):
            return self.model.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        from langsmith import tracing_context

        if self.before_request:
            self.before_request()
        with tracing_context(enabled=False):
            return self.model.embed_query(text)
