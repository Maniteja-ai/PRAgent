"""Validation for ``webhook.json``."""

from pathlib import Path
from typing import Literal

from pydantic import Field

from impact_agent.config.validation.common import StrictSettings


class WebhookConfig(StrictSettings):
    enabled: bool = True
    secret_env: str = "GITHUB_WEBHOOK_SECRET"
    accepted_event: Literal["pull_request"] = "pull_request"
    accepted_actions: tuple[Literal["opened", "synchronize", "reopened"], ...] = (
        "opened",
        "synchronize",
        "reopened",
    )
    max_body_bytes: int = Field(default=2_000_000, ge=1_024, le=10_000_000)
    database_path: Path = Path("data/webhook-jobs.sqlite3")
