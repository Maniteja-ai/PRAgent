"""Stateless Gemini Interactions transport behind the LangChain extractor contract."""

import logging

import httpx
from google import genai
from google.genai import types

# SDK 2.27 exposes Interactions errors here, separately from generateContent errors.
from google.genai._gaos.lib.compat_errors import APIError
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from trace_impact.ingestion.config import ExtractionConfig
from trace_impact.ingestion.models import Extraction
from trace_impact.ingestion.requirements.langchain_extractor import LangChainRequirementExtractor
from trace_impact.shared.errors import ExtractionError
from trace_impact.shared.events import JsonEventSink
from trace_impact.shared.rate_limit import RequestPacer
from trace_impact.shared.settings import Settings


def build_extractor(settings: Settings, config: ExtractionConfig):
    settings.require_gemini()
    client = genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        vertexai=False,
        http_options=types.HttpOptions(
            timeout=int(settings.request_timeout * 1000),
            # Unlike generateContent, Interactions interprets this as retries after the first call.
            retry_options=types.HttpRetryOptions(attempts=settings.model_retries),
        ),
    )
    # SDK 2.27 coerces HttpRetryOptions(attempts=0) to 1 before building this resource.
    # Set the translated retry budget explicitly; wire tests cover 0, 1 and 2 retries.
    try:
        interactions = client.interactions
        interactions.sdk_configuration.retry_config.max_retries = settings.model_retries
    except Exception:
        client.close()
        raise

    def invoke(messages):
        generation = {"max_output_tokens": config.max_output_tokens}
        if config.thinking_level is not None:
            generation["thinking_level"] = config.thinking_level
        result = interactions.create(
            model=config.model,
            system_instruction=messages[0].content,
            input=messages[1].content,
            store=False,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": Extraction.model_json_schema(),
            },
            generation_config=generation,
            timeout=settings.request_timeout,
        )
        if result.status != "completed" or not result.output_text:
            raise ExtractionError("Gemini interaction did not complete with structured output")
        try:
            parsed = Extraction.model_validate_json(result.output_text)
        except ValidationError as exc:
            raise ExtractionError("Gemini interaction returned invalid extraction JSON") from exc
        usage = result.usage
        metadata = None
        if usage is not None:
            metadata = {
                "input_tokens": usage.total_input_tokens or 0,
                "output_tokens": usage.total_output_tokens or 0,
                "total_tokens": usage.total_tokens or 0,
            }
        return {
            "parsed": parsed,
            "raw": AIMessage(
                content=result.output_text,
                response_metadata={"status": result.status},
                usage_metadata=metadata,
            ),
        }

    return LangChainRequirementExtractor(
        RunnableLambda(invoke),
        provider="gemini",
        model=config.model,
        configuration_id="gemini-interactions-json-schema-v1:" + config.model_dump_json(),
        events=JsonEventSink(logging.getLogger("trace_impact.events")),
        close=client.close,
        request_errors=(APIError, httpx.HTTPError),
        before_request=RequestPacer(config.requests_per_minute).wait,
    )
