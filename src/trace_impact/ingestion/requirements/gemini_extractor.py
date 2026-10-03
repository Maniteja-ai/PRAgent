"""Gemini adapters: LangChain extraction and SDK embeddings with retrieval-specific formatting."""

import logging

import httpx
from google.genai import errors
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai._common import GoogleGenerativeAIError

from trace_impact.ingestion.config import ExtractionConfig
from trace_impact.ingestion.models import Extraction
from trace_impact.ingestion.requirements.langchain_extractor import LangChainRequirementExtractor
from trace_impact.shared.events import JsonEventSink
from trace_impact.shared.rate_limit import RequestPacer
from trace_impact.shared.settings import Settings


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
