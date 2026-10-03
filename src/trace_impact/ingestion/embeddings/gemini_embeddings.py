"""Gemini embeddings."""

import httpx
from google import genai
from google.genai import errors, types

from trace_impact.ingestion.config import EmbeddingConfig
from trace_impact.ingestion.models import EmbeddingProfile
from trace_impact.shared.errors import ConfigurationError, ProviderError
from trace_impact.shared.rate_limit import RequestPacer
from trace_impact.shared.settings import Settings


class GeminiEmbeddingProvider:
    """Embedding 2 needs task prefixes, not the older task_type API field.

    Use explicit Content objects to retain one embedding per input document.
    """

    def __init__(self, client, profile: EmbeddingProfile, pacer: RequestPacer):
        self.client, self.profile, self.pacer = client, profile, pacer

    def close(self):
        self.client.close()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.pacer.wait()
        try:
            result = self.client.models.embed_content(
                model=self.profile.model,
                contents=[types.Content(parts=[types.Part(text=text)]) for text in texts],
                config=types.EmbedContentConfig(output_dimensionality=self.profile.dimensions),
            )
        except (errors.APIError, httpx.HTTPError) as exc:
            raise ProviderError(
                "Gemini embedding request failed; check credentials, quota and model access, then resume"
            ) from exc
        if not result.embeddings or len(result.embeddings) != len(texts):
            raise ProviderError("Gemini must return one embedding per input text")
        if any(not item.values for item in result.embeddings):
            raise ProviderError("Gemini returned an empty embedding")
        return [list(item.values) for item in result.embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([f"title: none | text: {text}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"task: search result | query: {text}"])[0]


def build_embeddings(settings: Settings, config: EmbeddingConfig):
    settings.require_gemini()
    if config.model != "gemini-embedding-2":
        raise ConfigurationError("Gemini embedding adapter currently supports gemini-embedding-2")
    if not 128 <= config.dimensions <= 3072:
        raise ConfigurationError("Gemini Embedding 2 dimensions must be between 128 and 3072")
    client = genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        vertexai=False,
        http_options=types.HttpOptions(
            timeout=int(settings.request_timeout * 1000),
            retry_options=types.HttpRetryOptions(attempts=settings.model_retries + 1),
        ),
    )
    return GeminiEmbeddingProvider(
        client,
        EmbeddingProfile(
            provider="gemini",
            model=config.model,
            dimensions=config.dimensions,
            preprocessing="gemini-embedding-2-search-v1",
        ),
        RequestPacer(config.requests_per_minute),
    )
