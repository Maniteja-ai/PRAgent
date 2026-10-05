"""Gemini embedding adapter selected entirely from typed configuration."""

import os
import time

from google import genai
from google.genai import types

from ingestion.beans.decorators import component
from ingestion.config_loader.models import ApplicationConfig
from ingestion.embedding.interface import EmbeddingProvider


@component(contract=EmbeddingProvider, name="gemini")
class GeminiEmbeddingProvider:
    def __init__(self, config: ApplicationConfig) -> None:
        embedding = config.models.embedding
        selected = embedding.model
        if embedding.implementation != "gemini" or selected is None or selected.provider != "gemini":
            raise ValueError("Gemini embeddings require a Gemini model configuration")
        if selected.api_key_env is None:
            raise ValueError("Gemini api_key_env is required")
        try:
            api_key = os.environ[selected.api_key_env]
        except KeyError as exc:
            raise ValueError(f"Required environment variable is missing: {exc.args[0]}") from exc
        self._model = selected.name
        self._batch_size = min(embedding.batch_size, 100)
        self._task_type = embedding.task_type
        if selected.requests_per_minute < 1:
            raise ValueError("Gemini embedding requests_per_minute must be at least 1")
        self._minimum_request_interval = 60.0 / selected.requests_per_minute
        self._attempts = config.constraints.retries.attempts + 1
        self._last_request = 0.0
        self._client = genai.Client(api_key=api_key, vertexai=False)

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        collected: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            wait = self._minimum_request_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            response = self._embed_batch(batch)
            if response.embeddings is None or len(response.embeddings) != len(batch):
                raise RuntimeError("Gemini did not return one embedding per input")
            collected.extend(tuple(item.values or ()) for item in response.embeddings)
        values = tuple(collected)
        dimensions = len(values[0]) if values else 0
        if dimensions == 0 or any(len(vector) != dimensions for vector in values):
            raise RuntimeError("Gemini returned inconsistent embedding dimensions")
        return values

    def _embed_batch(self, texts: tuple[str, ...]) -> types.EmbedContentResponse:
        for attempt in range(self._attempts):
            try:
                self._last_request = time.monotonic()
                return self._client.models.embed_content(
                    model=self._model,
                    contents=[
                        types.Content(parts=[types.Part(text=self._format_document(text))])
                        for text in texts
                    ],
                    config=types.EmbedContentConfig(
                        task_type=self._task_type
                    ),
                )
            except Exception:
                if attempt + 1 == self._attempts:
                    raise
                time.sleep(10.0 * (attempt + 1))
        raise RuntimeError("Gemini embedding retry loop terminated unexpectedly")

    def _format_document(self, text: str) -> str:
        if self._model == "gemini-embedding-2":
            return f"title: none | text: {text}"
        return text

    def close(self) -> None:
        self._client.close()
