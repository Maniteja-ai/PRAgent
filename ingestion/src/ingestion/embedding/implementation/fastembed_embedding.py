"""Free local semantic embeddings powered by FastEmbed and ONNX."""

from fastembed import TextEmbedding

from ingestion.beans.decorators import component
from ingestion.config_loader.models import ApplicationConfig
from ingestion.embedding.interface import EmbeddingProvider


@component(contract=EmbeddingProvider, name="fastembed")
class FastEmbedEmbeddingProvider:
    def __init__(self, config: ApplicationConfig) -> None:
        embedding = config.models.embedding
        selected = embedding.model
        if embedding.implementation != "fastembed" or selected is None:
            raise ValueError("FastEmbed requires a model configuration")
        self._batch_size = embedding.batch_size
        self._model = TextEmbedding(model_name=selected.name)

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors = tuple(
            tuple(float(value) for value in vector)
            for vector in self._model.embed(list(texts), batch_size=self._batch_size)
        )
        if len(vectors) != len(texts):
            raise RuntimeError("FastEmbed did not return one embedding per input")
        dimensions = len(vectors[0]) if vectors else 0
        if dimensions == 0 or any(len(vector) != dimensions for vector in vectors):
            raise RuntimeError("FastEmbed returned inconsistent embedding dimensions")
        return vectors

    def close(self) -> None:
        return None
