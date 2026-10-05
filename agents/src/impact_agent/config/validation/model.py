"""Validation for ``models.json``."""

from typing import Literal

from pydantic import Field, model_validator

from impact_agent.config.validation.common import StrictSettings


class ModelConfig(StrictSettings):
    provider: Literal["gemini", "openai", "fixture"]
    model: str
    api_key_env: str | None = None
    api_url: str = "https://generativelanguage.googleapis.com/v1beta"
    temperature: float = Field(default=0, ge=0, le=2)
    max_input_characters: int = Field(default=150_000, ge=1_000, le=1_000_000)
    embedding_model: Literal["gemini-embedding-001", "gemini-embedding-2"] = "gemini-embedding-2"
    embedding_task_type: Literal["RETRIEVAL_QUERY"] | None = None

    @model_validator(mode="after")
    def validate_embedding_settings(self) -> "ModelConfig":
        if self.embedding_model == "gemini-embedding-001" and self.embedding_task_type is None:
            raise ValueError("gemini-embedding-001 requires embedding_task_type=RETRIEVAL_QUERY")
        if self.embedding_model == "gemini-embedding-2" and self.embedding_task_type is not None:
            raise ValueError("gemini-embedding-2 uses query text formatting, not task_type")
        return self
