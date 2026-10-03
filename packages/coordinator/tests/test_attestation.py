import hashlib

import httpx
import pytest
from pydantic import ValidationError

from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import ToolContext
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.adapters.attestation import AttestationInput, DeploymentAttestationTool


def application(tmp_path, *, production=True):
    backend = "https://api.example/graphql/"
    fingerprint = hashlib.sha256(backend.encode()).hexdigest()
    app = ApplicationConfig(
        project_id="project",
        repository="owner/repo",
        repository_path=str(tmp_path),
        graph_snapshot_file="graph.json",
        ingestion_run_directory="ingestion",
        retrieval_config_file="retrieval.json",
        vector_directory="vectors",
        production_mode=production,
        baseline={
            "url": "https://baseline.example",
            "revision": "a" * 40,
            "attestation": {"path": "/api/trace-build"},
        },
        patched={
            "url": "https://patched.example",
            "revision": "b" * 40,
            "attestation": {"path": "/api/trace-build"},
        },
    )
    return app, fingerprint


def context():
    return ToolContext(run_id="run", project_id="project", agent_id="coordinator")


def test_attestation_binds_origin_revision_deployment_and_backend(tmp_path):
    app, fingerprint = application(tmp_path)

    def handle(request):
        assert request.url == "https://baseline.example/api/trace-build"
        assert request.headers["cache-control"] == "no-cache"
        return httpx.Response(
            200,
            json={
                "schema_version": 1,
                "status": "READY",
                "revision": "a" * 40,
                "deployment_id": "dpl_baseline",
                "backend_fingerprint": fingerprint,
                "channel": "default-channel",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = DeploymentAttestationTool(app, client).execute(
            AttestationInput(environment="baseline"), context()
        )
    evidence = result.evidence[0]
    assert evidence.kind == "attestation"
    assert evidence.metadata["revision"] == app.baseline.revision
    assert evidence.metadata["deployment_id"] == "dpl_baseline"
    assert evidence.source == "https://baseline.example/api/trace-build"


@pytest.mark.parametrize(
    "response,match",
    [
        (httpx.Response(503, json={"status": "UNAVAILABLE"}), "bounded success"),
        (
            httpx.Response(
                200,
                json={
                    "schema_version": 1,
                    "status": "READY",
                    "revision": "c" * 40,
                    "deployment_id": "dpl_changed",
                    "backend_fingerprint": "d" * 64,
                    "channel": "default-channel",
                },
            ),
            "does not match",
        ),
        (httpx.Response(200, json={"schema_version": 1}), "document is invalid"),
    ],
)
def test_bad_attestation_fails_closed(tmp_path, response, match):
    app, _ = application(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
    with client, pytest.raises(ToolFailure, match=match):
        DeploymentAttestationTool(app, client).execute(AttestationInput(environment="baseline"), context())


def test_production_configuration_requires_two_https_attestations(tmp_path):
    with pytest.raises(ValidationError, match="Production mode"):
        ApplicationConfig(
            project_id="project",
            repository="owner/repo",
            repository_path=str(tmp_path),
            graph_snapshot_file="graph.json",
            ingestion_run_directory="ingestion",
            retrieval_config_file="retrieval.json",
            vector_directory="vectors",
            production_mode=True,
            baseline={"url": "https://baseline.example", "revision": "a" * 40},
            patched={"url": "http://patched.example", "revision": "b" * 40},
        )
