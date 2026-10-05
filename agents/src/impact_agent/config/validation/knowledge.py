"""Validation for ``knowledge.json``."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from impact_agent.config.validation.common import StrictSettings


class KnowledgeConfig(StrictSettings):
    provider: Literal["qdrant", "disabled"] = "qdrant"
    project: str
    retrieval_profile: str = "balanced"
    max_evidence: int = Field(default=12, ge=1, le=100)
    qdrant_url_env: str | None = "QDRANT_URL"
    qdrant_api_key_env: str | None = "QDRANT_API_KEY"
    qdrant_path: Path | None = None
    collection: str = "saleor_knowledge"

    @model_validator(mode="after")
    def require_qdrant_connection(self) -> "KnowledgeConfig":
        if self.provider == "qdrant" and self.qdrant_url_env is None and self.qdrant_path is None:
            raise ValueError("Qdrant needs a URL environment name or a local storage path")
        return self
