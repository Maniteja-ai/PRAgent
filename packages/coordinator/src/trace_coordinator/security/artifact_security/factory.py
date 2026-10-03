"""Select the configured artifact scanner implementation."""

from typing import Any

from trace_coordinator.security.artifact_security.config import ArtifactSecurityConfig
from trace_coordinator.security.artifact_security.implementations import (
    BaselineArtifactScanner,
    GoogleArtifactScanner,
)
from trace_coordinator.security.artifact_security.interface import ArtifactScanner


def build_scanner(
    config: ArtifactSecurityConfig, *, client: Any | None = None, module: Any | None = None
) -> ArtifactScanner | None:
    provider = config.provider
    if provider.provider == "disabled":
        return None
    if provider.provider == "baseline":
        return BaselineArtifactScanner()
    return GoogleArtifactScanner(provider, client=client, module=module)
