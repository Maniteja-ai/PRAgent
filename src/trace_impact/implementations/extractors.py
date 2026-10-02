"""LangChain adapter: structured model IO, separated from evidence policy."""

import json
from collections.abc import Callable
from typing import Any

from trace_impact.errors import ExtractionError
from trace_impact.interfaces import EventSink
from trace_impact.models import Chunk, Extraction, Snapshot, stable_id
from trace_impact.prompts import PROMPT_VERSION, SYSTEM_PROMPT


class LangChainRequirementExtractor:
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        structured_model: Any,
        *,
        provider: str,
        model: str,
        configuration_id: str,
        events: EventSink | None = None,
        close: Callable[[], None] | None = None,
    ):
        # structured_model is a LangChain Runnable; injected so tests never need API credentials.
        self.structured_model = structured_model
        self.provider, self.model = provider, model
        self.events = events
        self._close = close
        self.fingerprint = stable_id(
            "langchain-v1",
            provider,
            model,
            configuration_id,
            SYSTEM_PROMPT,
            json.dumps(Extraction.model_json_schema(), sort_keys=True),
        )

    def close(self) -> None:
        if self._close is not None:
            self._close()
            self._close = None

    def extract(self, chunk: Chunk, snapshot: Snapshot, scope: list[str]) -> Extraction:
        from langchain_core.exceptions import OutputParserException
        from langchain_core.messages import HumanMessage, SystemMessage
        from openai import OpenAIError

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "scope": scope,
                        "authority": snapshot.authority,
                        "version": snapshot.version,
                        "heading": chunk.heading,
                        "documentation": chunk.text,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        try:
            # LangSmith tracing is disabled here even if inherited environment variables enable it.
            from langsmith import tracing_context

            with tracing_context(enabled=False):
                response = self.structured_model.invoke(
                    messages, config={"run_name": "requirement_extraction"}
                )
        except (OpenAIError, OutputParserException) as exc:
            raise ExtractionError("Model request failed or structured output could not be parsed") from exc
        if not isinstance(response, dict) or response.get("parsing_error") is not None:
            raise ExtractionError("Provider returned an invalid structured response")
        parsed = response.get("parsed")
        raw = response.get("raw")
        if self.events is not None:
            usage = getattr(raw, "usage_metadata", None) or {}
            self.events.emit(
                "model.usage",
                chunk_id=chunk.id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
        metadata = getattr(raw, "response_metadata", {})
        if metadata.get("finish_reason") in {"length", "content_filter"} or metadata.get("status") in {
            "incomplete",
            "failed",
        }:
            raise ExtractionError("Provider output was incomplete")
        if getattr(raw, "additional_kwargs", {}).get("refusal") or not isinstance(parsed, Extraction):
            raise ExtractionError("Provider refused or omitted the structured extraction")
        return parsed
