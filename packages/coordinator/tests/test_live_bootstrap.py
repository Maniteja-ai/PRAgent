"""Exercise the live composition and graph routing without external services."""

import json
from pathlib import Path

import pytest

from trace_coordinator.decision_model.implementations.fixture import FixtureDecisionModel
from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import AnalysisRequest, Evidence, ToolResult
from trace_coordinator.setup import create_coordinator
from trace_coordinator.tool.implementations.fixture import FixtureTool

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/coordinator/voucher-analysis.json").read_text())


@pytest.mark.parametrize("browser_failure", [False, True])
@pytest.mark.parametrize("change_provider", ["github", "local_git"])
@pytest.mark.parametrize("verification_enabled", [False, True])
def test_live_composition_routes_entry_captures_and_cleans_up(
    tmp_path, monkeypatch, browser_failure, change_provider, verification_enabled
):
    from trace_coordinator.decision_model.implementations import langchain
    from trace_coordinator.tool.implementations import browser, github, knowledge, local_git

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

    class Model(FixtureDecisionModel):
        def __init__(self, config):
            super().__init__([{"action": "finish"}])

        def close(self):
            closed.append("model")

    monkeypatch.setattr(github, "GitHubDiffTool", GitHub)
    monkeypatch.setattr(local_git, "LocalGitDiffTool", GitHub)
    monkeypatch.setattr(knowledge, "KnowledgeTool", Knowledge)
    monkeypatch.setattr(browser, "BrowserSession", Browser)
    monkeypatch.setattr(langchain, "LangChainDecisionModel", Model)
    app = json.loads((ROOT / "configs/application/saleor.json").read_text())
    # This composition test replaces all live adapters except attestation; keep it offline.
    app["require_exact_revisions"] = False
    app["repository_path"] = str(ROOT.parents[3] / "work/saleor-storefront-upstream")
    app["graph_config_file"] = str(ROOT / "configs/graph/saleor.json")
    app["retrieval_config_file"] = str(ROOT / "configs/retrieval/saleor.json")
    app["ui_config_file"] = str(ROOT / "configs/ui/saleor.json")
    if change_provider == "github":
        app["change_source"] = {"provider": "github"}
    (tmp_path / "app.json").write_text(json.dumps(app))
    config = json.loads((ROOT / "configs/saleor-live.json").read_text())
    config["application_config_file"] = "app.json"
    config.pop("runtime_config_file", None)
    config["env_file"] = "local.env"
    config["state_directory"] = "state"
    if verification_enabled:
        selected = json.loads((ROOT / "configs/verification/saleor-voucher.json").read_text())
        selected["application_file"] = "app.json"
        selected["state_directory"] = "unused"
        (tmp_path / "scenario.json").write_text(json.dumps(selected))
        policy = {
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
        (tmp_path / "policy.json").write_text(json.dumps(policy))
        config["verification_config_file"] = "policy.json"
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
            AnalysisRequest.model_validate_json((ROOT / "tests/fixtures/requests/demo.json").read_text()),
            "integration",
        )
    assert report["status"] == "COMPLETED"
    assert calls == [("browser.navigate", "baseline"), ("browser.navigate", "patched")]
    assert closed == (["model", "browser", "github"] if change_provider == "github" else ["model", "browser"])
    assert next(r["attempts"] for r in report["tool_usage"] if r["tool"] == "browser.navigate") == 2
    if browser_failure:
        assert any("browser.navigate failed" in g for g in report["gaps"])
    else:
        assert {"ui:baseline", "ui:patched"} <= report["evidence"].keys()
