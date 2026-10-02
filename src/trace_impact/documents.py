"""Compatibility facade for prior callers; new services receive reader strategies."""

import httpx

from .infrastructure.parsing import make_chunks, normalize
from .infrastructure.sources import HttpSourceReader, LocalFileReader

__all__ = ["make_chunks", "normalize", "read_source"]


def read_source(source, project, config_dir):
    if source.location.startswith(("http://", "https://")):
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            return HttpSourceReader(client).read(source, project, config_dir)
    return LocalFileReader().read(source, project, config_dir)
