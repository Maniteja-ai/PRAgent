"""Validation for ``github.json``."""

from typing import Literal

from pydantic import Field, model_validator

from impact_agent.config.validation.common import StrictSettings


class GitHubConfig(StrictSettings):
    provider: Literal["github_api"] = "github_api"
    api_url: str = "https://api.github.com"
    token_env: str = "GITHUB_TOKEN"
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_changed_files: int = Field(default=1000, ge=1, le=3000)
    max_diff_bytes: int = Field(default=1_000_000, ge=1_000, le=10_000_000)

    @model_validator(mode="after")
    def validate_github_api_url(self) -> "GitHubConfig":
        if not self.api_url.startswith(("https://", "http://localhost")):
            raise ValueError("api_url must use HTTPS (localhost is allowed for tests)")
        return self
