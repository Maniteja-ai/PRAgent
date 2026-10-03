"""Validated configuration and results for artifact scanning."""

from typing import Literal

from pydantic import Field

from trace_coordinator.domain.models import Record


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
