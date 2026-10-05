from pathlib import Path

import pytest

from impact_agent.config.loader.implementations.json_config_loader import (
    ConfigurationError,
    JsonConfigLoader,
)

CONFIG = Path(__file__).parents[1] / "config" / "default"


def test_loads_separate_configuration_as_typed_settings():
    settings = JsonConfigLoader().load(CONFIG)

    assert settings.models.model == "gemini-3.8-flash"
    assert settings.github.max_diff_evidence_characters == 60_000
    assert settings.runtime.max_calls_per_tool == 5
    assert settings.browser.allowed_hosts == ("testsigma-saleor-patched.vercel.app",)
    assert settings.agent.browser_enabled
    assert settings.agent.behavior_checks_enabled
    assert settings.behavior.provider == "playwright"
    assert {scenario.scenario_id for scenario in settings.behavior.scenarios} == {
        "cart-page-renders",
        "product-list-renders",
        "checkout-discount-controls-render",
    }
    assert settings.guardrails.require_evidence_for_findings


def test_rejects_unknown_configuration_fields(tmp_path):
    for path in CONFIG.glob("*.json"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text('{"max_calls_per_tool":5,"typo":true}', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="runtime.json"):
        JsonConfigLoader().load(tmp_path)


def test_reports_missing_configuration_file(tmp_path):
    with pytest.raises(ConfigurationError, match="agent.json"):
        JsonConfigLoader().load(tmp_path)
