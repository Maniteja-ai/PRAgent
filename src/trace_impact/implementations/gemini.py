"""Gemini adapters: LangChain extraction and SDK embeddings with retrieval-specific formatting."""

import logging

import httpx
from google import genai
from google.genai import errors, types
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai._common import GoogleGenerativeAIError

from ..config import Settings
from ..errors import ConfigurationError, ProviderError
from ..events import JsonEventSink
from ..models import EmbeddingConfig, EmbeddingProfile, Extraction, ExtractionConfig
from .extractors import LangChainRequirementExtractor
from .rate_limit import RequestPacer


def build_extractor(settings: Settings, config: ExtractionConfig):
    settings.require_gemini()
    llm = ChatGoogleGenerativeAI(
        model=config.model,
        api_key=settings.gemini_api_key,
        vertexai=False,
        timeout=settings.request_timeout,
        # Google counts attempts, including the first request; 0 means SDK defaults.
        max_retries=settings.model_retries + 1,
        max_output_tokens=config.max_output_tokens,
        thinking_level=config.thinking_level,
    )
    try:
        return LangChainRequirementExtractor(
            llm.with_structured_output(Extraction, method="json_schema", include_raw=True),
            provider="gemini",
            model=config.model,
            configuration_id="gemini-json-schema-v1:" + config.model_dump_json(),
            events=JsonEventSink(logging.getLogger("trace_impact.events")),
            close=llm.client.close,
            request_errors=(errors.APIError, GoogleGenerativeAIError, httpx.HTTPError),
            before_request=RequestPacer(config.requests_per_minute).wait,
        )
    except Exception:
        llm.client.close()
        raise


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
