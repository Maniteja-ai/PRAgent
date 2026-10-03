import json

import pytest

from trace_coordinator.security.artifact_security import (
    ArtifactScan,
    ArtifactSecurityConfig,
    GoogleArtifactScanner,
    artifact_security,
    build_scanner,
)
from trace_coordinator.storage.artifacts import save_artifact


def test_baseline_dlp_blocks_sensitive_artifact_and_audits_only_categories(tmp_path):
    config = ArtifactSecurityConfig.model_validate(
        {
            "provider": {"provider": "baseline"},
            "unsupported_action": "allow",
        }
    )
    with artifact_security(tmp_path, config):
        with pytest.raises(ValueError, match="email"):
            save_artifact(tmp_path, "run", b'{"email":"shopper@example.com"}', ".json")
    audit = (tmp_path / "security/artifact-dlp.jsonl").read_text()
    assert "shopper@example.com" not in audit
    event = json.loads(audit)
    assert event["status"] == "SENSITIVE"
    assert event["findings"] == {"email": 1}


def test_artifact_dlp_fails_closed_on_scanner_error_and_unsupported_type(tmp_path):
    class Broken:
        version = "broken"

        def inspect(self, *_):
            raise TimeoutError

    config = ArtifactSecurityConfig.model_validate({"provider": {"provider": "baseline"}})
    with artifact_security(tmp_path, config, scanner=Broken()):
        with pytest.raises(ValueError, match="scanning failed"):
            save_artifact(tmp_path, "run", b"safe", ".json")

    with artifact_security(tmp_path, config):
        with pytest.raises(ValueError, match="unsupported"):
            save_artifact(tmp_path, "run", b"binary", ".zip")


def test_artifact_dlp_scans_every_write_including_binary(tmp_path):
    class Recording:
        version = "recording"

        def __init__(self):
            self.calls = []

        def inspect(self, data, suffix, _config):
            self.calls.append((data, suffix))
            return ArtifactScan(status="CLEAN", provider=self.version)

    scanner = Recording()
    config = ArtifactSecurityConfig.model_validate({"provider": {"provider": "baseline"}})
    with artifact_security(tmp_path, config, scanner=scanner):
        save_artifact(tmp_path, "run", b"screen", ".png")
        save_artifact(tmp_path, "run", b"{}", ".json")
    assert scanner.calls == [(b"screen", ".png"), (b"{}", ".json")]


def test_google_dlp_adapter_sends_no_quotes_and_returns_categories(monkeypatch):
    monkeypatch.setenv("DLP_PROJECT", "project-1")

    class Finding:
        class Info:
            name = "EMAIL_ADDRESS"

        info_type = Info()

    class Response:
        class Result:
            findings = [Finding(), Finding()]

        result = Result()

    class Client:
        def __init__(self):
            self.request = None

        def inspect_content(self, *, request, timeout):
            self.request, self.timeout = request, timeout
            return Response()

    class Module:
        class Likelihood:
            POSSIBLE = 2

        class ByteContentItem:
            class BytesType:
                IMAGE = 1

    config = ArtifactSecurityConfig.model_validate(
        {
            "provider": {
                "provider": "google_dlp",
                "project_id_env": "DLP_PROJECT",
                "info_types": ["EMAIL_ADDRESS"],
            }
        }
    )
    client = Client()
    scanner = GoogleArtifactScanner(config.provider, client=client, module=Module)
    result = scanner.inspect(b"contact shopper@example.com", ".json", config)
    assert result.status == "SENSITIVE"
    assert result.findings == {"EMAIL_ADDRESS": 2}
    assert client.request["parent"] == "projects/project-1/locations/global"
    assert client.request["inspect_config"]["include_quote"] is False


def test_google_dlp_scans_images_and_rejects_missing_project(monkeypatch):
    class Client:
        def inspect_content(self, *, request, timeout):
            self.request, self.timeout = request, timeout

            class Result:
                findings = []

            return type("Response", (), {"result": Result()})()

    class Module:
        class Likelihood:
            POSSIBLE = 2

        class ByteContentItem:
            class BytesType:
                IMAGE = 1

    config = ArtifactSecurityConfig.model_validate(
        {"provider": {"provider": "google_dlp", "project_id_env": "DLP_PROJECT"}}
    )
    monkeypatch.delenv("DLP_PROJECT", raising=False)
    with pytest.raises(ValueError, match="Missing Google DLP project"):
        GoogleArtifactScanner(config.provider, client=Client(), module=Module)

    monkeypatch.setenv("DLP_PROJECT", "project-1")
    client = Client()
    scanner = GoogleArtifactScanner(config.provider, client=client, module=Module)
    assert scanner.inspect(b"pixels", ".png", config).status == "CLEAN"
    assert client.request["item"]["byte_item"]["data"] == b"pixels"
    assert scanner.inspect(b"value", ".zip", config).status == "UNSUPPORTED"


def test_artifact_policy_size_failure_allow_and_duplicate_registration(tmp_path):
    class Broken:
        version = "broken"

        def inspect(self, *_):
            raise TimeoutError

    allow = ArtifactSecurityConfig.model_validate(
        {
            "provider": {"provider": "baseline"},
            "scanner_error_action": "allow",
            "unsupported_action": "allow",
            "max_bytes": 1000,
        }
    )
    with artifact_security(tmp_path, allow, scanner=Broken()):
        save_artifact(tmp_path, "run", b"safe", ".json")
        with pytest.raises(RuntimeError, match="already configured"):
            with artifact_security(tmp_path, allow, scanner=Broken()):
                pass
        with pytest.raises(ValueError, match="size policy"):
            save_artifact(tmp_path, "run", b"x" * 1001, ".json")
    assert build_scanner(ArtifactSecurityConfig()) is None
