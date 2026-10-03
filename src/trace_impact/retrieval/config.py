"""Stage names and typed options; JSON selects registered code, never imports it."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from trace_impact.shared.contracts import Contract
from trace_impact.shared.stage_config import StageConfig


class RetrievalConfig(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    candidate_limit: int = Field(
        default=5, ge=1, le=100, description="Vector candidates; increase explicitly when reranking."
    )
    reranker: StageConfig = Field(default_factory=lambda: StageConfig(provider="identity"))
    selector: StageConfig = Field(default_factory=lambda: StageConfig(provider="top_k"))


class TopKOptions(Contract):
    max_results: int = Field(default=5, ge=1, le=100)


class ThresholdOptions(TopKOptions):
    score_kind: str = Field(min_length=1, description="Must match the reranker's score scale exactly.")
    min_score: float = Field(description="Inclusive relevance cutoff; not a probability.")
    deduplicate_text: bool = Field(default=True, description="Remove exact duplicate text after ranking.")


class LLMRerankerOptions(Contract):
    model: str = Field(min_length=1)
    api_key_env: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Environment variable NAME, never the API-key value. Explicit names never fall back.",
    )
    base_url: str | None = Field(
        default=None, description="Provider API endpoint; null uses its SDK default."
    )
    requests_per_minute: int = Field(default=5, ge=1, le=1000)
    timeout_seconds: float = Field(default=45, gt=0, le=300)
    max_retries: int = Field(default=1, ge=0, le=3)
    max_output_tokens: int = Field(default=6000, ge=512, le=16000)
    max_input_chars: int = Field(
        default=120000, ge=1000, le=500000, description="Fail rather than silently truncate passage evidence."
    )

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value):
        if value is None:
            return value
        url = urlsplit(value)
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or not (
                url.scheme == "https"
                or (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
            )
        ):
            raise ValueError(
                "Use an HTTPS API endpoint (HTTP allowed for loopback), without credentials or query"
            )
        return value


class GeminiRerankerOptions(LLMRerankerOptions):
    thinking_level: Literal["low", "medium", "high"] | None = Field(
        default="low", description="Set null for Gemini models that do not support thinking_level."
    )


class CompatibleLLMOptions(LLMRerankerOptions):
    api_key_env: str = Field(default="ENCODER_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    base_url: str = Field(description="Chat Completions API base URL, usually ending in /v1.")
    structured_output: Literal["json_schema", "json_mode"] = Field(
        default="json_schema",
        description="Choose a JSON mode supported by this provider/model. No automatic fallback.",
    )


class CrossEncoderOptions(Contract):
    model: str = Field(min_length=1, description="Hugging Face sequence-classification model with one logit.")
    revision: str = Field(pattern=r"^[a-f0-9]{40}$", description="Immutable model and tokenizer commit SHA.")
    cache_directory: str = Field(default=".model-cache", min_length=1)
    local_files_only: bool = Field(default=False, description="Use True after downloading the pinned model.")
    batch_size: int = Field(default=8, ge=1, le=64)
    max_length: int = Field(
        default=512, ge=64, le=4096, description="Query, passage window and special tokens."
    )
    max_query_tokens: int = Field(
        default=128, ge=1, le=512, description="Longer queries fail without truncation."
    )
    overlap_tokens: int = Field(default=64, ge=0, le=256)
    max_windows: int = Field(
        default=256, ge=1, le=2048, description="Total windows per request; overflow fails."
    )
    max_input_chars: int = Field(default=120000, ge=1000, le=500000)

    @property
    def score_kind(self) -> str:
        return (
            f"cross-encoder-logit-v1:{self.model}@{self.revision}"
            f":length={self.max_length}:overlap={self.overlap_tokens}:aggregation=max"
        )


def load_retrieval_config(path: Path) -> RetrievalConfig:
    return RetrievalConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))


def retrieval_schema(components) -> dict:
    """Generate provider suggestions plus provider-specific option definitions."""
    from trace_impact.shared.schema import add_stage_schemas

    schema = RetrievalConfig.model_json_schema()
    schema.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Retrieval stages",
            "description": "The retriever uses the selected ingestion run. Replace stages through registered providers.",
        }
    )
    return add_stage_schemas(schema, (("reranker", components.rerankers), ("selector", components.selectors)))
