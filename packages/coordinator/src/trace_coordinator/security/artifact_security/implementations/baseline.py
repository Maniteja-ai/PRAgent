"""Local regex-based artifact scanner."""

from trace_coordinator.security.artifact_security.config import ArtifactScan, ArtifactSecurityConfig
from trace_coordinator.security.guardrails import findings


class BaselineArtifactScanner:
    version = "baseline-regex-v1"

    def inspect(self, data: bytes, suffix: str, config: ArtifactSecurityConfig) -> ArtifactScan:
        if not any(suffix.endswith(item) for item in config.text_suffixes):
            return ArtifactScan(status="UNSUPPORTED", provider=self.version)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ArtifactScan(status="UNSUPPORTED", provider=self.version)
        detected = {key: value for key, value in findings(text).items() if key != "prompt_injection"}
        return ArtifactScan(
            status="SENSITIVE" if detected else "CLEAN",
            findings=detected,
            provider=self.version,
        )
