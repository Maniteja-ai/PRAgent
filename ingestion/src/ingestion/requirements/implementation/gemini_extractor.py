"""Structured Gemini requirement extraction with source evidence."""

import hashlib
import os
import time

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, ValidationError

from ingestion.beans.decorators import component
from ingestion.config_loader.models import ApplicationConfig
from ingestion.domain.models import Chunk, Requirement
from ingestion.requirements.interface import RequirementExtractor


class ExtractedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    evidence: str
    source_chunk_id: str


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: tuple[ExtractedRequirement, ...]


def _gemini_response_schema() -> types.Schema:
    """Return the small schema subset supported by Gemini's responseSchema field."""
    requirement_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "statement": types.Schema(type=types.Type.STRING),
            "evidence": types.Schema(type=types.Type.STRING),
            "source_chunk_id": types.Schema(type=types.Type.STRING),
        },
        required=["statement", "evidence", "source_chunk_id"],
    )
    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "requirements": types.Schema(
                type=types.Type.ARRAY,
                items=requirement_schema,
            )
        },
        required=["requirements"],
    )


@component(contract=RequirementExtractor, name="gemini")
class GeminiRequirementExtractor:
    def __init__(self, config: ApplicationConfig) -> None:
        extraction = config.models.requirement_extraction
        selected = extraction.model
        if extraction.implementation != "gemini" or selected is None or selected.provider != "gemini":
            raise ValueError("Gemini requirement extraction requires a Gemini model configuration")
        if selected.api_key_env is None:
            raise ValueError("Gemini api_key_env is required")
        try:
            api_key = os.environ[selected.api_key_env]
        except KeyError as exc:
            raise ValueError(f"Required environment variable is missing: {exc.args[0]}") from exc
        self._client = genai.Client(api_key=api_key, vertexai=False)
        self._model = selected.name
        self._max_chunks = extraction.max_chunks
        self._batch_size = extraction.batch_size
        self._attempts = config.constraints.retries.attempts + 1
        self._retry_delay = 1.0
        if selected.requests_per_minute < 1:
            raise ValueError("Gemini requests_per_minute must be at least 1")
        self._minimum_interval = 60.0 / selected.requests_per_minute
        self._last_request = 0.0

    def extract(self, chunks: tuple[Chunk, ...]) -> tuple[Requirement, ...]:
        requirements: list[Requirement] = []
        selected_chunks = chunks[: self._max_chunks]
        known_ids = {chunk.id for chunk in selected_chunks}
        for start in range(0, len(selected_chunks), self._batch_size):
            batch = selected_chunks[start : start + self._batch_size]
            response = self._request(batch)
            parsed = self._parse_response(response)
            for item in parsed.requirements:
                if item.source_chunk_id not in known_ids:
                    raise RuntimeError("Gemini cited an unknown source chunk")
                identifier = hashlib.sha256(
                    f"{item.source_chunk_id}:{item.statement}:{item.evidence}".encode()
                ).hexdigest()
                requirements.append(
                    Requirement(
                        id=identifier,
                        statement=item.statement,
                        source_chunk_id=item.source_chunk_id,
                        evidence=item.evidence,
                    )
                )
        return tuple(requirements)

    def _request(self, chunks: tuple[Chunk, ...]) -> types.GenerateContentResponse:
        sources = "\n\n".join(f"CHUNK_ID={chunk.id}\n{chunk.content}" for chunk in chunks)
        prompt = (
            "Extract only explicit, testable product requirements. Copy a short supporting "
            "evidence phrase and return its exact CHUNK_ID. Return an empty list when none exist.\n\n"
            + sources
        )
        for attempt in range(self._attempts):
            wait = self._minimum_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_request = time.monotonic()
                return self._client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=_gemini_response_schema(),
                    ),
                )
            except Exception:
                if attempt + 1 == self._attempts:
                    raise
                time.sleep(self._retry_delay * (attempt + 1))
        raise RuntimeError("Gemini retry loop terminated unexpectedly")

    @staticmethod
    def _parse_response(response: types.GenerateContentResponse) -> ExtractionResponse:
        parsed = response.parsed
        try:
            if isinstance(parsed, ExtractionResponse):
                return parsed
            if isinstance(parsed, dict):
                return ExtractionResponse.model_validate(parsed)
            if response.text:
                return ExtractionResponse.model_validate_json(response.text)
        except ValidationError as exc:
            raise RuntimeError("Gemini returned an invalid requirement response") from exc
        raise RuntimeError("Gemini returned an empty requirement response")

    def close(self) -> None:
        self._client.close()
