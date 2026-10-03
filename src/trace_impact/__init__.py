"""TraceImpact: configurable ingestion with replaceable stage implementations."""

from trace_impact.pipeline import IngestionPipeline
from trace_impact.shared.registry import Components
from trace_impact.shared.settings import Settings

__version__ = "0.5.0"
__all__ = ["create_pipeline", "IngestionPipeline", "Components", "Settings"]


def create_pipeline(settings: Settings | None = None) -> IngestionPipeline:
    from trace_impact.bootstrap import default_components

    selected = settings or Settings()
    return IngestionPipeline(default_components(selected), selected, default_components)
