from typing import Protocol

from ingestion.config_loader.models import CodeInputConfig
from ingestion.domain.models import CodeGraph


class CodeExtractor(Protocol):
    def extract(self, config: CodeInputConfig) -> CodeGraph: ...
