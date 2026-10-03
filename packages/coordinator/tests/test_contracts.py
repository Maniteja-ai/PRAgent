import ast
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from trace_coordinator.config import (
    CallLimits,
    CoordinatorFile,
    RuntimeConfig,
    VerificationPolicy,
    load_config,
    schema,
)
from trace_coordinator.domain.models import Decision
from trace_coordinator.runtime_defaults import runtime_defaults

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("value", [0, 6, -1, 100])
def test_five_is_hard_config_ceiling(value):
    with pytest.raises(ValidationError):
        CallLimits(per_agent_tool=value)


@pytest.mark.parametrize("value", [0, 6, True, "3"])
def test_overrides_cannot_bypass_limits(value):
    with pytest.raises(ValidationError):
        CallLimits(overrides={"coordinator": {"search": value}})


def test_overrides_cannot_raise_lower_default():
    with pytest.raises(ValidationError):
        CallLimits(per_agent_tool=2, overrides={"coordinator": {"search": 3}})


def test_json_schema_matches_runtime_and_example():
    jsonschema.Draft202012Validator.check_schema(schema())
    for path in (ROOT / "configs").glob("*.json"):
        example = json.loads(path.read_text(encoding="utf-8"))
        if path.name.endswith("application.json"):
            continue
        jsonschema.validate(example, schema())
        CoordinatorFile.model_validate(example)
    assert json.loads((ROOT / "schemas/coordinator.schema.json").read_text(encoding="utf-8")) == schema()


def test_runtime_file_resolves_calls_retries_and_timeouts():
    config = load_config(ROOT / "configs/saleor-verified.json")
    assert config.limits.total_calls == 35
    assert config.limits.max_rounds == 5
    assert config.limits.retry_attempts == 2
    assert config.limits.retry_delay_seconds == 2
    assert config.limits.max_run_seconds == 900
    assert config.model.provider == "gemini"
    assert config.model.timeout_seconds == 45
    assert config.model.retry_invalid_response is True


def test_omitting_runtime_file_applies_safe_defaults():
    config = load_config(ROOT / "tests/fixtures/configs/demo.json")
    assert config.limits.per_agent_tool == 5
    assert config.limits.total_calls == 30
    assert config.limits.max_rounds == 10
    assert config.limits.max_review_requests == 2
    assert config.limits.max_validation_repairs == 1
    assert config.limits.retry_attempts == 2
    assert config.limits.retry_delay_seconds == 1
    assert config.limits.max_run_seconds == 900
    assert config.guardrails.enabled is True
    assert config.observability.provider == "disabled"


def test_production_config_directory_contains_no_fixture_providers():
    for path in (ROOT / "configs").glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert "tool_fixture_file" not in document
        assert document.get("model", {}).get("provider") != "fixture"


def test_offline_fixture_profiles_are_isolated_and_loadable():
    fixture_root = ROOT / "tests/fixtures/configs"
    for name in ("demo.json", "limit-demo.json"):
        config = load_config(fixture_root / name)
        assert config.tool_provider.provider == "fixture"


def test_runtime_schema_and_standard_example_match():
    schema_document = json.loads((ROOT / "schemas/runtime.schema.json").read_text(encoding="utf-8"))
    example = json.loads((ROOT / "configs/runtime/standard.json").read_text(encoding="utf-8"))
    jsonschema.validate(example, schema_document)
    RuntimeConfig.model_validate(example)


def test_packaged_runtime_defaults_are_valid_and_complete():
    schema_document = json.loads((ROOT / "schemas/runtime.schema.json").read_text(encoding="utf-8"))
    defaults_path = ROOT / "configs/defaults/runtime.json"
    document = json.loads(defaults_path.read_text(encoding="utf-8"))
    assert document == runtime_defaults()
    jsonschema.validate(document, schema_document)
    resolved = RuntimeConfig.model_validate(document)
    assert resolved.call_limits() == CallLimits()
    assert resolved.guardrails.enabled is True


def test_verification_policy_schema_and_example_match():
    schema_document = json.loads(
        (ROOT / "schemas/verification-policy.schema.json").read_text(encoding="utf-8")
    )
    example = json.loads((ROOT / "configs/verification/saleor-policy.json").read_text(encoding="utf-8"))
    jsonschema.validate(example, schema_document)
    VerificationPolicy.model_validate(example)


def test_unknown_options_are_not_ignored():
    with pytest.raises(ValidationError):
        CallLimits(per_agent_tool=5, unlimited=True)


def test_contradictory_decision_rejected():
    with pytest.raises(ValidationError):
        Decision(action="finish", tool="search")


def test_package_core_does_not_depend_on_knowledge_library():
    for path in (ROOT / "src/trace_coordinator").rglob("*.py"):
        if path.name == "knowledge.py" and path.parent.name == "adapters":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("trace_impact")
            if isinstance(node, ast.Import):
                assert all(not item.name.startswith("trace_impact") for item in node.names)
