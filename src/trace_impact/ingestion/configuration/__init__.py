"""Typed, composed configuration for a complete ingestion run."""

from trace_impact.ingestion.configuration.loader import load_ingestion_configuration
from trace_impact.ingestion.configuration.models import IngestionConfiguration

__all__ = ["IngestionConfiguration", "load_ingestion_configuration"]
