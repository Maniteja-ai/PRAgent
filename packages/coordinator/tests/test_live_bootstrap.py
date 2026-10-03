"""Exercise the live composition and graph routing without external services."""

import json
from pathlib import Path

import pytest

from trace_coordinator.bootstrap import create_coordinator
from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import AnalysisRequest, Evidence, ToolResult
from trace_coordinator.infrastructure.adapters.fixtures import FixtureModel, FixtureTool

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "examples/voucher-fixture.json").read_text())


@pytest.mark.parametrize("browser_failure", [False, True])
@pytest.mark.parametrize("change_provider", ["github", "local_git"])
@pytest.mark.parametrize("verification_enabled", [False, True])
def test_live_composition_routes_entry_captures_and_cleans_up(
    tmp_path, monkeypatch, browser_failure, change_provider, verification_enabled
):
    from trace_coordinator.infrastructure.adapters import (
        browser,
        github,
        knowledge,
        langchain_model,
        local_git,
    )

    closed, calls = [], []

    class GitHub(FixtureTool):
        def __init__(self, app, root):
            super().__init__("github.diff", FIXTURE["tools"]["github.diff"])

        def close(self):
            closed.append("github")

    class Knowledge(FixtureTool):
        def __init__(self, name, app, root):
            super().__init__(name, FIXTURE["tools"][name])

    class Browser:
        def __init__(self, app, root):
            self.app = app

        def execute(self, name, args, context):
            calls.append((name, args.environment))
            if browser_failure:
                raise ToolFailure("Site is unavailable")
            return ToolResult(
                evidence=(
                    Evidence(
                        id="ui:" + args.environment,
                        project_id=context.project_id,
                        kind="browser",
                        summary="Local composition fixture",
                        source="fixture",
                    ),
                )
            )

        def close(self):
            closed.append("browser")

    class Model(FixtureModel):
        def __init__(self, config):
            super().__init__([{"action": "finish"}])

        def close(self):
            closed.append("model")

    monkeypatch.setattr(github, "GitHubDiffTool", GitHub)
    monkeypatch.setattr(local_git, "LocalGitDiffTool", GitHub)
    monkeypatch.setattr(knowledge, "KnowledgeTool", Knowledge)
    monkeypatch.setattr(browser, "BrowserSession", Browser)
    monkeypatch.setattr(langchain_model, "LangChainModel", Model)
    app = json.loads((ROOT / "configs/saleor-application.json").read_text())
    # This composition test replaces all live adapters except attestation; keep it offline.
    app["production_mode"] = False
    app["baseline"].pop("attestation", None)
    app["patched"].pop("attestation", None)
    if change_provider == "github":
        app["change_source"] = {"provider": "github"}
    (tmp_path / "app.json").write_text(json.dumps(app))
    config = json.loads((ROOT / "configs/saleor-live.json").read_text())
    config["tool_provider"]["application_config_file"] = "app.json"
    config["env_file"] = "local.env"
    config["state_directory"] = "state"
    if verification_enabled:
        selected = json.loads((ROOT / "configs/verification/saleor-voucher.json").read_text())
        selected["application_file"] = "app.json"
        selected["state_directory"] = "unused"
        (tmp_path / "scenario.json").write_text(json.dumps(selected))
        config["verification"] = {
            "enabled": True,
            "approval": "preapproved",
            "scenarios": [
                {
                    "id": "voucher",
                    "description": "Voucher test",
                    "config_file": "scenario.json",
                    "changed_paths": ["src/*"],
                }
            ],
        }
    (tmp_path / "local.env").write_text("TRACE_TEST_BOOTSTRAP=loaded\n")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    # Restore environment after dotenv loads, and prove config-relative resolution.
    monkeypatch.delenv("TRACE_TEST_BOOTSTRAP", raising=False)
    monkeypatch.chdir(tmp_path.parent)
    with create_coordinator(path) as coordinator:
        import os

        assert os.environ["TRACE_TEST_BOOTSTRAP"] == "loaded"
        report = coordinator.run(
            AnalysisRequest.model_validate_json((ROOT / "examples/request.json").read_text()), "integration"
        )
    assert report["status"] == "COMPLETED"
    assert calls == [("browser.navigate", "baseline"), ("browser.navigate", "patched")]
    assert closed == (["model", "browser", "github"] if change_provider == "github" else ["model", "browser"])
    assert next(r["attempts"] for r in report["tool_usage"] if r["tool"] == "browser.navigate") == 2
    if browser_failure:
        assert any("browser.navigate failed" in g for g in report["gaps"])
    else:
        assert {"ui:baseline", "ui:patched"} <= report["evidence"].keys()
