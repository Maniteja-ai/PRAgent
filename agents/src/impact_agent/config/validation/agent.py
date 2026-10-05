"""Validation for ``agent.json``."""

from pathlib import Path

from pydantic import Field

from impact_agent.config.validation.common import StrictSettings


class AgentConfig(StrictSettings):
    name: str = "testsigma-pr-impact-agent"
    schema_version: int = Field(default=1, ge=1)
    env_file: Path | None = None
    browser_enabled: bool = False
    behavior_checks_enabled: bool = False
