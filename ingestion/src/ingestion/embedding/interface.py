from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...

    def close(self) -> None: ...
