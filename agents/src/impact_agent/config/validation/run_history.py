"""Validation for ``run_history.json``."""

from pathlib import Path
from typing import Literal

from impact_agent.config.validation.common import StrictSettings


class RunHistoryConfig(StrictSettings):
    provider: Literal["sqlite"] = "sqlite"
    database_path: Path = Path("data/agent-runs.sqlite3")
