import hashlib

from ingestion.beans.decorators import component
from ingestion.embedding.interface import EmbeddingProvider


@component(contract=EmbeddingProvider, name="deterministic")
class DeterministicEmbeddingProvider:
    """Offline test provider; production providers implement the same interface."""

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode()).digest()
        return tuple(value / 255.0 for value in digest[:8])

    def close(self) -> None:
        return None
