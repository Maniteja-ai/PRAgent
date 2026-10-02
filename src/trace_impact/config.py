"""Validated runtime configuration; secrets are masked in object representations."""

import os

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"
    openai_api_key: SecretStr = SecretStr("")
    ingestion_model: str = ""
    request_timeout: float = Field(default=90, gt=0, le=600)
    model_retries: int = Field(default=2, ge=0, le=5)
    max_output_tokens: int = Field(default=6000, ge=256, le=32000)
    embedding_model: str = ""
    embedding_dimensions: int = Field(default=0, ge=0, le=65536)
    qdrant_url: str = ""
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_path: str = ".vector-store"

    @classmethod
    def from_env(cls):
        return cls(
            **{name: os.environ[name.upper()] for name in cls.model_fields if name.upper() in os.environ}
        )

    def require_model(self) -> None:
        if not self.ingestion_model or not self.openai_api_key.get_secret_value():
            raise ValueError("Set INGESTION_MODEL and OPENAI_API_KEY in local .env")

    def require_database(self) -> None:
        if not self.neo4j_uri or not self.neo4j_username or not self.neo4j_password.get_secret_value():
            raise ValueError("Set NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD in local .env")

    def require_embeddings(self) -> None:
        if (
            not self.embedding_model
            or not self.embedding_dimensions
            or not self.openai_api_key.get_secret_value()
        ):
            raise ValueError("Set EMBEDDING_MODEL, EMBEDDING_DIMENSIONS and OPENAI_API_KEY in local .env")
