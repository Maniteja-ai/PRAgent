import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from trace_coordinator.config import GeminiProvider, OpenAIProvider
from trace_coordinator.domain.errors import FailureCode, ToolFailure
from trace_coordinator.infrastructure.adapters.langchain_model import (
    LangChainModel,
    ModelDecision,
    classify_failure,
)
from trace_coordinator.presentation.cli import main

ROOT = Path(__file__).resolve().parents[1]


class FakeChat:
    def __init__(self, **options):
        self.options = options
        self.client = SimpleNamespace(close=self.close)
        self.closed = False
        self.error = None
        self.calls = 0

    def with_structured_output(self, schema, method):
        assert schema is ModelDecision
        assert method == "json_schema"
        return self

    def invoke(self, messages):
        self.calls += 1
        assert messages[0][0] == "system"
        if self.error:
            raise self.error
        return ModelDecision(action="finish", tool="", question="", arguments_json="{}", findings=[])

    def close(self):
        self.closed = True


@pytest.fixture
def model_stubs(monkeypatch):
    monkeypatch.setenv("TEST_MODEL_KEY", "test-only-secret")
    monkeypatch.setitem(
        sys.modules, "langchain_google_genai", SimpleNamespace(ChatGoogleGenerativeAI=FakeChat)
    )
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=FakeChat))


@pytest.mark.parametrize(
    "provider,kind,retries", [("gemini", GeminiProvider, 1), ("openai", OpenAIProvider, 0)]
)
def test_model_adapters_disable_sdk_retry_and_close(model_stubs, provider, kind, retries):
    adapter = LangChainModel(kind(provider=provider, model="test-model", api_key_env="TEST_MODEL_KEY"))
    assert adapter.client.options["max_retries"] == retries
    assert adapter.decide({"round": 1}).action == "finish"
    assert "test-only-secret" not in adapter.version
    adapter.close()
    assert adapter.client.closed


def test_missing_key_is_explicit(monkeypatch):
    monkeypatch.delenv("ABSENT_COORDINATOR_TEST_KEY", raising=False)
    with pytest.raises(ValueError, match="ABSENT_COORDINATOR_TEST_KEY"):
        LangChainModel(
            GeminiProvider(provider="gemini", model="test", api_key_env="ABSENT_COORDINATOR_TEST_KEY")
        )


def test_context_overflow_prevents_model_dispatch(model_stubs):
    adapter = LangChainModel(
        GeminiProvider(provider="gemini", model="test", api_key_env="TEST_MODEL_KEY", max_input_chars=1000)
    )
    with pytest.raises(ToolFailure, match="context budget"):
        adapter.decide({"text": "x" * 1001})
    assert adapter.client.calls == 0


def test_transient_errors_are_safe_and_retryable(model_stubs):
    adapter = LangChainModel(GeminiProvider(provider="gemini", model="test", api_key_env="TEST_MODEL_KEY"))
    adapter.client.error = TimeoutError("raw secret provider error")
    with pytest.raises(ToolFailure) as caught:
        adapter.decide({})
    assert caught.value.retryable
    assert "raw secret" not in str(caught.value)


@pytest.mark.parametrize("arguments", ["not-json", "[]", '"' + "x" * 16001 + '"'])
def test_provider_wire_still_has_strict_local_validation(model_stubs, monkeypatch, arguments):
    adapter = LangChainModel(GeminiProvider(provider="gemini", model="test", api_key_env="TEST_MODEL_KEY"))
    monkeypatch.setattr(
        adapter.structured,
        "invoke",
        lambda *args: ModelDecision(
            action="tool",
            tool="browser.act",
            arguments_json=arguments,
            question="",
            findings=[],
        ),
    )
    with pytest.raises(ToolFailure) as caught:
        adapter.decide({})
    assert not caught.value.retryable


def test_wire_does_not_emit_unsupported_schema_keywords():
    def check(value):
        if isinstance(value, dict):
            assert not {"const", "default", "minLength", "maxLength"} & value.keys()
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(ModelDecision.model_json_schema())


@pytest.mark.parametrize(
    "status,code,retry",
    [
        (503, FailureCode.PROVIDER_TRANSIENT, True),
        (401, FailureCode.PROVIDER_AUTH, False),
        (400, FailureCode.PROVIDER_INVALID_REQUEST, False),
        (418, FailureCode.PROVIDER_FAILURE, False),
    ],
)
def test_wrapped_provider_status_is_classified_without_raw_messages(status, code, retry):
    cause = RuntimeError("secret body never persisted")
    cause.code = status
    wrapped = RuntimeError("SDK wrapper")
    wrapped.__cause__ = cause
    assert classify_failure(wrapped) == (code, retry)


def test_structured_parser_failure_is_not_a_transient_provider_error():
    try:
        ModelDecision.model_validate({})
    except ValueError as exc:
        assert classify_failure(exc) == (FailureCode.MODEL_RESPONSE_INVALID, False)


@pytest.mark.parametrize("sdk_parser", [False, True])
def test_opt_in_invalid_output_retry_is_counted_and_strict(model_stubs, monkeypatch, tmp_path, sdk_parser):
    from trace_coordinator.application.runtime import ToolRegistry, ToolRuntime
    from trace_coordinator.config import CallLimits
    from trace_coordinator.domain.models import ToolContext
    from trace_coordinator.infrastructure.ledger import CallLedger

    adapter = LangChainModel(
        GeminiProvider(
            provider="gemini", model="test", api_key_env="TEST_MODEL_KEY", retry_invalid_response=True
        )
    )
    calls = []

    def invoke(*args):
        calls.append(1)
        if len(calls) == 1:
            if sdk_parser:
                ModelDecision.model_validate({})
            return ModelDecision(
                action="tool", tool="browser.act", arguments_json="not-json", question="", findings=[]
            )
        return ModelDecision(action="finish", tool="", arguments_json="{}", question="", findings=[])

    monkeypatch.setattr(adapter.structured, "invoke", invoke)
    ledger = CallLedger(tmp_path / "calls.sqlite")
    ledger.register("run", "f")
    runtime = ToolRuntime(
        ledger, CallLimits(retry_attempts=2, retry_delay_seconds=0), ToolRegistry([]), adapter
    )
    result = runtime.decide(ToolContext(run_id="run", project_id="p", agent_id="coordinator"), "reason:1", {})
    assert result.action == "finish" and len(calls) == 2
    usage = ledger.usage("run")
    assert usage[0]["attempts"] == 2 and usage[0]["failed"] == 1 and usage[0]["succeeded"] == 1


def test_cli_generates_schema_and_reports(tmp_path, monkeypatch, capsys):
    output_schema = tmp_path / "schema.json"
    monkeypatch.setattr(sys, "argv", ["trace-coordinator", "schema", str(output_schema)])
    main()
    assert json.loads(output_schema.read_text(encoding="utf-8"))["title"] == "PR impact coordinator"
    config = json.loads((ROOT / "configs/demo.json").read_text(encoding="utf-8"))
    config["state_directory"] = str(tmp_path / "state")
    for part in ("tools", "model"):
        config[part]["file"] = str(ROOT / "examples/voucher-fixture.json")
    selected = tmp_path / "config.json"
    selected.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "trace-coordinator",
            "run",
            str(selected),
            str(ROOT / "examples/request.json"),
            "--run-id",
            "cli-test",
            "--output",
            str(tmp_path / "report"),
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out)["status"] == "COMPLETED"
    assert (tmp_path / "report/report.md").is_file()
    assert (
        json.loads((tmp_path / "report/report.json").read_text(encoding="utf-8"))["verification"] == "NOT_RUN"
    )
