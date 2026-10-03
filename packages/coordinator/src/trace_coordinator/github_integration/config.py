"""Validated GitHub webhook configuration."""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from trace_coordinator.domain.contracts import JsonObject, as_json_object
from trace_coordinator.domain.models import Record


class GitHubAppCredentials(Record):
    app_id_env: str = Field(default="GITHUB_APP_ID", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    private_key_env: str = Field(default="GITHUB_APP_PRIVATE_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    private_key_file_env: str = Field(
        default="GITHUB_APP_PRIVATE_KEY_FILE", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    api_version: str = Field(default="2026-03-10", pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")


class GitHubWebhookConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    webhook_secret_env: str = Field(default="GITHUB_WEBHOOK_SECRET", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    allowed_repositories: tuple[str, ...] = Field(min_length=1, max_length=100)
    accepted_actions: tuple[Literal["opened", "reopened", "synchronize", "ready_for_review"], ...] = (
        "opened",
        "reopened",
        "synchronize",
        "ready_for_review",
    )
    database_file: str
    coordinator_config_file: str
    output_directory: str
    project_id: str = Field(min_length=1, max_length=200)
    question: str = Field(default="Which UI flows and requirements could this PR affect?", max_length=4000)
    publish: Literal["disabled", "comment"] = "disabled"
    github_app: GitHubAppCredentials = Field(default_factory=GitHubAppCredentials)
    max_payload_bytes: int = Field(default=1_000_000, ge=1000, le=10_000_000)

    @field_validator("allowed_repositories")
    @classmethod
    def valid_repositories(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        pattern = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
        if len(set(value.casefold() for value in values)) != len(values) or any(
            not pattern.fullmatch(value) for value in values
        ):
            raise ValueError("Repositories must be unique owner/name values")
        return values


def load_github_webhook_config(path: str | Path) -> GitHubWebhookConfig:
    selected = Path(path).resolve()
    config = GitHubWebhookConfig.model_validate_json(selected.read_text(encoding="utf-8-sig"))
    return config.model_copy(
        update={
            name: str((selected.parent / getattr(config, name)).resolve())
            for name in ("database_file", "coordinator_config_file", "output_directory")
        }
    )


def github_webhook_schema() -> JsonObject:
    return as_json_object(
        {
            **GitHubWebhookConfig.model_json_schema(),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "GitHub webhook integration",
        }
    )
