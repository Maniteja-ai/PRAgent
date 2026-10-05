from pathlib import Path
from typing import Protocol

from ingestion.config_loader.models import ApplicationConfig


class ConfigLoader(Protocol):
    def load(self, path: Path) -> ApplicationConfig: ...
