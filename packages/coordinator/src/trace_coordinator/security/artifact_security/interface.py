"""Contract implemented by artifact scanners."""

from typing import Protocol

from trace_coordinator.security.artifact_security.config import ArtifactScan, ArtifactSecurityConfig


class ArtifactScanner(Protocol):
    version: str

    def inspect(self, data: bytes, suffix: str, config: ArtifactSecurityConfig) -> ArtifactScan: ...
