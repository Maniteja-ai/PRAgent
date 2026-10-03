"""Google Cloud DLP artifact scanner."""

import hashlib
import json
import os
from collections import Counter
from typing import Any

from trace_coordinator.security.artifact_security.config import (
    ArtifactScan,
    ArtifactSecurityConfig,
    GoogleDlp,
)


class GoogleArtifactScanner:
    def __init__(self, provider: GoogleDlp, client: Any | None = None, module: Any | None = None) -> None:
        project = os.environ.get(provider.project_id_env)
        if not project:
            raise ValueError(f"Missing Google DLP project environment variable: {provider.project_id_env}")
        if client is None or module is None:
            try:
                from google.cloud import dlp_v2
            except ImportError as exc:
                raise ValueError("Install the coordinator 'dlp' extra for google_dlp") from exc
            module = dlp_v2
            client = dlp_v2.DlpServiceClient()
        self.provider = provider
        self.client = client
        self.module = module
        self.project = project
        self.version = (
            "google-dlp-v1:"
            + hashlib.sha256(
                json.dumps(provider.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()
        )

    def inspect(self, data: bytes, suffix: str, config: ArtifactSecurityConfig) -> ArtifactScan:
        is_text = any(suffix.endswith(item) for item in config.text_suffixes)
        is_image = any(suffix.endswith(item) for item in config.image_suffixes)
        if not is_text and not is_image:
            return ArtifactScan(status="UNSUPPORTED", provider=self.version)
        item: dict[str, object]
        if is_text:
            try:
                item = {"value": data.decode("utf-8")}
            except UnicodeDecodeError:
                return ArtifactScan(status="UNSUPPORTED", provider=self.version)
        else:
            item = {
                "byte_item": {
                    "type_": self.module.ByteContentItem.BytesType.IMAGE,
                    "data": data,
                }
            }
        response = self.client.inspect_content(
            request={
                "parent": f"projects/{self.project}/locations/{self.provider.location}",
                "inspect_config": {
                    "info_types": [{"name": name} for name in self.provider.info_types],
                    "min_likelihood": getattr(self.module.Likelihood, self.provider.min_likelihood),
                    "include_quote": False,
                    "limits": {"max_findings_per_request": self.provider.max_findings},
                },
                "item": item,
            },
            timeout=self.provider.timeout_seconds,
        )
        detected = Counter(item.info_type.name for item in response.result.findings)
        return ArtifactScan(
            status="SENSITIVE" if detected else "CLEAN",
            findings=dict(sorted(detected.items())),
            provider=self.version,
        )
