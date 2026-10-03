"""Pluggable DLP enforcement for every content-addressed artifact write."""

import hashlib
import json
import os
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from trace_coordinator.domain.models import Record
from trace_coordinator.security.guardrails import findings


class DisabledDlp(Record):
    provider: Literal["disabled"] = "disabled"


class BaselineDlp(Record):
    provider: Literal["baseline"] = "baseline"


class GoogleDlp(Record):
    provider: Literal["google_dlp"] = "google_dlp"
    project_id_env: str = Field(default="GOOGLE_CLOUD_PROJECT", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    location: str = Field(default="global", pattern=r"^[A-Za-z0-9-]{1,63}$")
    info_types: tuple[str, ...] = Field(
        default=(
            "PERSON_NAME",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "STREET_ADDRESS",
            "CREDIT_CARD_NUMBER",
            "US_SOCIAL_SECURITY_NUMBER",
            "AUTH_TOKEN",
            "JSON_WEB_TOKEN",
        ),
        min_length=1,
        max_length=100,
    )
    min_likelihood: Literal["POSSIBLE", "LIKELY", "VERY_LIKELY"] = "POSSIBLE"
    max_findings: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: float = Field(default=15, gt=0, le=60)


class ArtifactSecurityConfig(Record):
    provider: DisabledDlp | BaselineDlp | GoogleDlp = Field(
        default_factory=DisabledDlp, discriminator="provider"
    )
    sensitive_action: Literal["block"] = "block"
    scanner_error_action: Literal["block", "allow"] = "block"
    unsupported_action: Literal["block", "allow"] = "block"
    max_bytes: int = Field(default=1_000_000, ge=1_000, le=10_000_000)
    text_suffixes: tuple[str, ...] = (
        ".json",
        ".html",
        ".txt",
        ".md",
        ".diff",
        ".observation.json",
        ".transition.json",
        ".assertion.json",
        ".fixture.json",
        ".behavior.json",
        ".graph.json",
    )
    image_suffixes: tuple[str, ...] = (".png", ".jpg", ".jpeg")


class ArtifactScan(Record):
    status: Literal["CLEAN", "SENSITIVE", "UNSUPPORTED"]
    findings: dict[str, int] = Field(default_factory=dict)
    provider: str


class ArtifactScanner(Protocol):
    version: str

    def inspect(self, data: bytes, suffix: str, config: ArtifactSecurityConfig) -> ArtifactScan: ...


class BaselineArtifactScanner:
    version = "baseline-regex-v1"

    def inspect(self, data, suffix, config):
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


class GoogleArtifactScanner:
    def __init__(self, provider, client=None, module=None):
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
        self.provider, self.client, self.module, self.project = provider, client, module, project
        self.version = (
            "google-dlp-v1:"
            + hashlib.sha256(
                json.dumps(provider.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()
        )

    def inspect(self, data, suffix, config):
        is_text = any(suffix.endswith(item) for item in config.text_suffixes)
        is_image = any(suffix.endswith(item) for item in config.image_suffixes)
        if not is_text and not is_image:
            return ArtifactScan(status="UNSUPPORTED", provider=self.version)
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
        inspect_config = {
            "info_types": [{"name": name} for name in self.provider.info_types],
            "min_likelihood": getattr(self.module.Likelihood, self.provider.min_likelihood),
            "include_quote": False,
            "limits": {"max_findings_per_request": self.provider.max_findings},
        }
        response = self.client.inspect_content(
            request={
                "parent": f"projects/{self.project}/locations/{self.provider.location}",
                "inspect_config": inspect_config,
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


_LOCK = threading.RLock()
_POLICIES = {}


def build_scanner(config, *, client=None, module=None):
    provider = config.provider
    if provider.provider == "disabled":
        return None
    if provider.provider == "baseline":
        return BaselineArtifactScanner()
    return GoogleArtifactScanner(provider, client=client, module=module)


@contextmanager
def artifact_security(root, config, *, scanner=None):
    root = Path(root).resolve()
    scanner = scanner if scanner is not None else build_scanner(config)
    if scanner is None:
        yield
        return
    with _LOCK:
        if root in _POLICIES:
            raise RuntimeError("Artifact security is already configured for this root")
        _POLICIES[root] = (config, scanner)
    try:
        yield
    finally:
        with _LOCK:
            _POLICIES.pop(root, None)


def _audit(root, event):
    directory = root / "security"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "artifact-dlp.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def enforce_artifact_policy(root, data, suffix):
    root = Path(root).resolve()
    with _LOCK:
        selected = _POLICIES.get(root)
    if selected is None:
        return
    config, scanner = selected
    event = {
        "at": time.time(),
        "bytes": len(data),
        "suffix": suffix,
        "sha256": hashlib.sha256(data).hexdigest(),
        "scanner": scanner.version,
    }
    if len(data) > config.max_bytes:
        event.update(status="BLOCKED", reason="size_limit")
        _audit(root, event)
        raise ValueError("Artifact blocked by DLP size policy")
    try:
        result = scanner.inspect(data, suffix, config)
    except Exception as exc:
        event.update(status="BLOCKED", reason="scanner_error", error=type(exc).__name__)
        _audit(root, event)
        if config.scanner_error_action == "block":
            raise ValueError("Artifact blocked because DLP scanning failed") from exc
        return
    event.update(status=result.status, findings=result.findings)
    _audit(root, event)
    if result.status == "SENSITIVE":
        raise ValueError("Artifact blocked by DLP policy: " + ", ".join(result.findings))
    if result.status == "UNSUPPORTED" and config.unsupported_action == "block":
        raise ValueError("Artifact blocked because its content type is unsupported by DLP")
