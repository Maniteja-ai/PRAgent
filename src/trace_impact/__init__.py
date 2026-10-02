"""TraceImpact: configurable ingestion with replaceable stage implementations."""

from .config import Settings
from .pipeline import IngestionPipeline
from .registry import Components

__version__ = "0.4.0"
__all__ = ["create_pipeline", "IngestionPipeline", "Components", "Settings"]


def create_pipeline(settings: Settings | None = None) -> IngestionPipeline:
    from .bootstrap import default_components

    return IngestionPipeline(default_components(settings or Settings()))
