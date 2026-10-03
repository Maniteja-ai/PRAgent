"""Application inputs are separate from execution policy and model selection."""

from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from trace_coordinator.models import Record

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


class RuntimeAttestation(Record):
    path: str = Field(
        default="/api/trace-build",
        description="Same-origin JSON endpoint that reports the running build and backend identity.",
    )

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        if (
            not value.startswith("/")
            or value.startswith("//")
            or "\\" in value
            or "?" in value
            or "#" in value
        ):
            raise ValueError("Attestation path must stay within the deployment origin")
        return value


class Deployment(Record):
    url: str
    revision: Sha
    entry_path: str = "/"
    attestation: RuntimeAttestation | None = None

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTP(S) application origin without credentials or query")
        if parsed.path not in {"", "/"}:
            raise ValueError("Set the route in entry_path, not url")
        return value.rstrip("/")

    @field_validator("entry_path")
    @classmethod
    def relative_path(cls, value):
        if not value.startswith("/") or value.startswith("//") or "\\" in value:
            raise ValueError("Use an absolute path within the application origin")
        return value


class BrowserOptions(Record):
    enabled: bool = True
    headless: bool = True
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    settle_ms: int = Field(default=500, ge=0, le=3000)
    max_elements: int = Field(default=80, ge=1, le=200)
    max_text_chars: int = Field(default=16000, ge=1000, le=30000)
    max_dom_chars: int = Field(default=300000, ge=1000, le=1000000)
    allowed_button_names: tuple[str, ...] = Field(
        default=(), description="Exact visible/accessible names authorized for button clicks."
    )
    allowed_button_test_ids: tuple[str, ...] = Field(
        default=(),
        description="Exact observed data-testid values allowed for buttons whose accessible name changes (for example cart item counts).",
    )
    allowed_field_names: tuple[str, ...] = Field(
        default=(), description="Exact labels or placeholders permitted for fill/select actions."
    )
    readiness_checks: dict[str, str] = Field(
        default_factory=dict,
        description="Optional route-prefix -> visible Playwright locator. Wait before capture; longest matching prefix wins. Uses the browser timeout, never clicks or fills.",
    )


class LocalGitSource(Record):
    provider: Literal["local_git"] = "local_git"
    pull_request: int = Field(gt=0, description="User-supplied PR label; not verified against GitHub.")
    base_revision: Sha = Field(description="Full commit ID already present in the local repository.")
    head_revision: Sha = Field(description="Full commit ID of the proposed change, already present locally.")
    historical_replay: bool = Field(
        default=False, description="User-supplied replay label; no remote status lookup."
    )


class GitHubSource(Record):
    provider: Literal["github"] = "github"


class ApplicationConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    project_id: str
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    repository_path: str
    change_source: LocalGitSource | GitHubSource = Field(
        default_factory=GitHubSource,
        discriminator="provider",
        description="Where revision identities come from. local_git makes no GitHub requests or automatic fetches.",
    )
    graph_snapshot_file: str
    head_graph_snapshot_file: str | None = None
    ingestion_run_directory: str
    retrieval_config_file: str
    vector_directory: str
    baseline: Deployment
    patched: Deployment
    github_token_env: str | None = None
    github_timeout_seconds: int = Field(default=30, ge=1, le=120)
    git_timeout_seconds: int = Field(default=45, ge=1, le=120)
    max_changed_files: int = Field(default=100, ge=1, le=100)
    max_patch_chars: int = Field(default=50000, ge=1000, le=55000)
    production_mode: bool = Field(
        default=False,
        description="Fail before analysis unless both HTTPS deployments attest their exact revisions.",
    )
    browser: BrowserOptions = Field(default_factory=BrowserOptions)

    @model_validator(mode="after")
    def separate_environments(self):
        if self.baseline.url == self.patched.url:
            raise ValueError("Baseline and patched environments must have separate origins")
        if self.production_mode and any(
            deployment.attestation is None or not deployment.url.startswith("https://")
            for deployment in (self.baseline, self.patched)
        ):
            raise ValueError("Production mode requires HTTPS runtime attestation for both deployments")
        return self


def load_application(path: Path) -> ApplicationConfig:
    config = ApplicationConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    fields = (
        "repository_path",
        "graph_snapshot_file",
        "head_graph_snapshot_file",
        "ingestion_run_directory",
        "retrieval_config_file",
        "vector_directory",
    )
    return config.model_copy(
        update={
            name: str((path.parent / getattr(config, name)).resolve())
            for name in fields
            if getattr(config, name) is not None
        }
    )
