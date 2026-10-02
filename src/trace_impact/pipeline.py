"""Compatibility facade; workflows now live in injected application services."""

import logging
from pathlib import Path

from .application.ports import RequirementExtractor
from .application.services import ExtractionService
from .bootstrap import ApplicationContainer
from .domain.policies import GroundingPolicy
from .infrastructure.artifacts import FileArtifactRepository
from .infrastructure.events import JsonEventSink
from .settings import Settings


def collect(config: Path, output_root: Path):
    with ApplicationContainer(Settings()) as app:
        return app.collection().collect(config, output_root)


def extract(run_dir: Path, extractor: RequirementExtractor, max_chunks: int = 100):
    service = ExtractionService(
        extractor,
        FileArtifactRepository(),
        GroundingPolicy(),
        JsonEventSink(logging.getLogger("trace_impact.events")),
    )
    return service.extract(run_dir, max_chunks)
