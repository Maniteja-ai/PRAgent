"""Fail-closed, same-origin runtime deployment attestation."""

import time
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import Field

from trace_coordinator.errors import FailureCode, ToolFailure
from trace_coordinator.ledger import canonical, digest
from trace_coordinator.models import Evidence, Record, ToolResult


class AttestationInput(Record):
    environment: Literal["baseline", "patched"]


class AttestationDocument(Record):
    schema_version: Literal[1]
    status: Literal["READY"]
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    deployment_id: str = Field(min_length=3, max_length=200)
    backend_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel: str = Field(min_length=1, max_length=100)


class DeploymentAttestationTool:
    name = "deployment.attest"
    description = "Verify one configured deployment's exact running revision and backend identity."
    input_model = AttestationInput
    allowed_agents = frozenset({"coordinator"})

    def __init__(self, application, client=None):
        self.app = application
        self.client = client or httpx.Client(
            timeout=application.github_timeout_seconds, follow_redirects=False
        )
        self._owns_client = client is None
        self.version = "http-attestation-v1:" + digest(
            {
                environment: getattr(application, environment).model_dump(mode="json")
                for environment in ("baseline", "patched")
            }
        )

    def close(self):
        if self._owns_client:
            self.client.close()

    def execute(self, arguments, context):
        if context.project_id != self.app.project_id:
            raise ToolFailure("Attestation application is outside the project")
        deployment = getattr(self.app, arguments.environment)
        if deployment.attestation is None:
            raise ToolFailure("Runtime attestation is not configured")
        target = urljoin(deployment.url + "/", deployment.attestation.path.lstrip("/"))
        if f"{urlsplit(target).scheme}://{urlsplit(target).netloc}" != deployment.url:
            raise ToolFailure("Attestation endpoint leaves the configured origin")
        try:
            response = self.client.get(
                target, headers={"Accept": "application/json", "Cache-Control": "no-cache"}
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ToolFailure(
                "Runtime attestation endpoint is unavailable",
                retryable=True,
                code=FailureCode.PROVIDER_TRANSIENT,
            ) from exc
        if response.status_code != 200 or len(response.content) > 4096:
            raise ToolFailure("Runtime attestation endpoint did not return a bounded success response")
        try:
            document = AttestationDocument.model_validate(response.json())
        except ValueError as exc:
            raise ToolFailure("Runtime attestation document is invalid") from exc
        if document.revision != deployment.revision:
            raise ToolFailure("Runtime deployment revision does not match configuration")
        record = {
            **document.model_dump(mode="json"),
            "environment": arguments.environment,
            "origin": deployment.url,
            "captured_at": time.time(),
        }
        return ToolResult(
            evidence=(
                Evidence(
                    id=f"attestation:{arguments.environment}:" + digest(record)[:20],
                    project_id=context.project_id,
                    kind="attestation",
                    summary=canonical(record),
                    source=target,
                    metadata=record,
                ),
            )
        )
