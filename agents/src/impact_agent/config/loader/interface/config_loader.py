"""Interface for loading the complete typed agent configuration."""

from pathlib import Path
from typing import Protocol

from impact_agent.config.settings import AgentSettings


class ConfigLoader(Protocol):
    def load(self, directory: Path) -> AgentSettings: ...
