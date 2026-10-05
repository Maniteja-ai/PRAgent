from typing import Protocol

from ingestion.config_loader.models import UiInputConfig
from ingestion.domain.models import UiObservation


class UiExtractor(Protocol):
    def extract(self, config: UiInputConfig) -> tuple[UiObservation, ...]: ...
