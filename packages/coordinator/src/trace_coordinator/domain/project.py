"""Small user-facing application files composed into strict runtime configuration."""

import json
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter, field_validator, model_validator

from trace_coordinator.domain.models import Record

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


class RuntimeAttestation(Record):
    path: str = Field(
        default="/api/trace-build",
        description="Same-origin JSON endpoint that reports the running build and backend identity.",
    )

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
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
    def public_url(cls, value: str) -> str:
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
    def relative_path(cls, value: str) -> str:
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


class GraphConfigFile(Record):
    """Neo4j inputs kept out of the application file."""

    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    provider: Literal["neo4j"] = "neo4j"
    baseline_snapshot_file: str = Field(min_length=1)
    patched_snapshot_file: str | None = Field(default=None, min_length=1)


class RetrievalStageConfig(Record):
    provider: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    options: dict[str, object] = Field(default_factory=dict)


class RetrievalConfigFile(Record):
    """Document store and ranking settings kept together in one file."""

    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    provider: Literal["qdrant"] = "qdrant"
    ingestion_run_directory: str = Field(min_length=1)
    vector_directory: str = Field(min_length=1)
    candidate_limit: int = Field(default=5, ge=1, le=100)
    reranker: RetrievalStageConfig = Field(default_factory=lambda: RetrievalStageConfig(provider="identity"))
    selector: RetrievalStageConfig = Field(
        default_factory=lambda: RetrievalStageConfig(provider="top_k", options={"max_results": 5})
    )


class DisabledUIConfigFile(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    provider: Literal["disabled"] = "disabled"


class PlaywrightUIConfigFile(BrowserOptions):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    provider: Literal["playwright"] = "playwright"
    entry_path: str = "/"

    @field_validator("entry_path")
    @classmethod
    def safe_entry_path(cls, value: str) -> str:
        return Deployment.relative_path(value)


class DeploymentFile(Record):
    url: str
    revision: Sha
    attestation_path: str = "/api/trace-build"

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        return Deployment.public_url(value)

    @field_validator("attestation_path")
    @classmethod
    def safe_attestation_path(cls, value: str) -> str:
        return RuntimeAttestation.safe_path(value)


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


class ApplicationFile(Record):
    """The only application document a user edits directly."""

    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    repository_path: str = Field(min_length=1)
    change_source: LocalGitSource | GitHubSource = Field(
        default_factory=GitHubSource, discriminator="provider"
    )
    graph_config_file: str = Field(min_length=1)
    retrieval_config_file: str = Field(min_length=1)
    ui_config_file: str = Field(min_length=1)
    baseline: DeploymentFile
    patched: DeploymentFile
    require_exact_revisions: bool = True

    @model_validator(mode="after")
    def separate_environments(self) -> Self:
        if self.baseline.url == self.patched.url:
            raise ValueError("Baseline and patched environments must have separate origins")
        if self.require_exact_revisions and any(
            not deployment.url.startswith("https://") for deployment in (self.baseline, self.patched)
        ):
            raise ValueError("Exact revision checks require HTTPS deployments")
        return self


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
    def separate_environments(self) -> Self:
        if self.baseline.url == self.patched.url:
            raise ValueError("Baseline and patched environments must have separate origins")
        if self.production_mode and any(
            deployment.attestation is None or not deployment.url.startswith("https://")
            for deployment in (self.baseline, self.patched)
        ):
            raise ValueError("Production mode requires HTTPS runtime attestation for both deployments")
        return self


def load_application(path: Path) -> ApplicationConfig:
    resolved = path.resolve()
    selected = ApplicationFile.model_validate_json(resolved.read_text(encoding="utf-8-sig"))
    graph_path = (resolved.parent / selected.graph_config_file).resolve()
    retrieval_path = (resolved.parent / selected.retrieval_config_file).resolve()
    ui_path = (resolved.parent / selected.ui_config_file).resolve()
    graph = GraphConfigFile.model_validate_json(graph_path.read_text(encoding="utf-8-sig"))
    retrieval = RetrievalConfigFile.model_validate_json(retrieval_path.read_text(encoding="utf-8-sig"))
    ui_document = json.loads(ui_path.read_text(encoding="utf-8-sig"))
    provider = ui_document.get("provider")
    if provider == "playwright":
        ui = PlaywrightUIConfigFile.model_validate(ui_document)
        browser = BrowserOptions.model_validate(
            ui.model_dump(exclude={"schema_reference", "schema_version", "provider", "entry_path"})
        )
        entry_path = ui.entry_path
    elif provider == "disabled":
        DisabledUIConfigFile.model_validate(ui_document)
        browser, entry_path = BrowserOptions(enabled=False), "/"
    else:
        raise ValueError(f"Unsupported UI provider: {provider!r}")

    def deployment(value: DeploymentFile) -> Deployment:
        return Deployment(
            url=value.url,
            revision=value.revision,
            entry_path=entry_path,
            attestation=(
                RuntimeAttestation(path=value.attestation_path) if selected.require_exact_revisions else None
            ),
        )

    return ApplicationConfig(
        project_id=selected.project_id,
        repository=selected.repository,
        repository_path=str((resolved.parent / selected.repository_path).resolve()),
        change_source=selected.change_source,
        graph_snapshot_file=str((graph_path.parent / graph.baseline_snapshot_file).resolve()),
        head_graph_snapshot_file=(
            str((graph_path.parent / graph.patched_snapshot_file).resolve())
            if graph.patched_snapshot_file
            else None
        ),
        ingestion_run_directory=str((retrieval_path.parent / retrieval.ingestion_run_directory).resolve()),
        retrieval_config_file=str(retrieval_path),
        vector_directory=str((retrieval_path.parent / retrieval.vector_directory).resolve()),
        baseline=deployment(selected.baseline),
        patched=deployment(selected.patched),
        production_mode=selected.require_exact_revisions,
        browser=browser,
    )


def _schema(model: type[Record], title: str) -> dict[str, object]:
    return {
        **model.model_json_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
    }


def application_schema() -> dict[str, object]:
    return _schema(ApplicationFile, "Application")


def graph_schema() -> dict[str, object]:
    return _schema(GraphConfigFile, "Code graph")


def retrieval_schema() -> dict[str, object]:
    return _schema(RetrievalConfigFile, "Document retrieval")


def ui_schema() -> dict[str, object]:
    return {
        **TypeAdapter(DisabledUIConfigFile | PlaywrightUIConfigFile).json_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "UI evidence",
    }
