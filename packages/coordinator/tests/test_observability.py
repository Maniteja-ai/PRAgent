import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from trace_coordinator.config import LangSmithObservability
from trace_coordinator.ledger import CallLedger
from trace_coordinator.models import AnalysisRequest
from trace_coordinator.observability import CoordinatorObservability, TraceSession

REQUEST = AnalysisRequest(
    project_id="saleor-storefront",
    repository="saleor/storefront",
    pull_request=1199,
)


def test_content_capture_cannot_be_enabled():
    with pytest.raises(ValidationError):
        LangSmithObservability(provider="langsmith", capture_content=True)


def test_disabled_provider_creates_no_report_section(tmp_path):
    observer = CoordinatorObservability()
    session = observer.start(run_id="run-0", request=REQUEST, workflow_version="v1")
    ledger = CallLedger(tmp_path / "calls.sqlite")
    ledger.register("run-0", "fingerprint")
    result = observer.attach(ledger, "run-0", {"status": "COMPLETED"}, session)
    assert result == {"status": "COMPLETED"}
    assert ledger.events("run-0") == []


def test_missing_key_is_fail_open_and_audited(tmp_path, monkeypatch):
    monkeypatch.delenv("ABSENT_LANGSMITH_KEY", raising=False)
    observer = CoordinatorObservability(
        LangSmithObservability(provider="langsmith", api_key_env="ABSENT_LANGSMITH_KEY")
    )
    session = observer.start(run_id="run-1", request=REQUEST, workflow_version="v1")
    assert session.graph_options() == {}
    ledger = CallLedger(tmp_path / "calls.sqlite")
    ledger.register("run-1", "fingerprint")
    result = observer.attach(ledger, "run-1", {"status": "COMPLETED"}, session)
    assert result["status"] == "COMPLETED"
    assert result["observability"]["status"] == "UNAVAILABLE"
    assert result["observability"]["content_captured"] is False
    assert len(result["observability_events"]) == 1


def test_langsmith_callback_hides_content_and_uses_safe_metadata(monkeypatch):
    from trace_coordinator import observability

    captured = {}

    class FakeClient:
        def __init__(self, **options):
            captured["client"] = options

        def flush(self, timeout):
            captured["flush"] = timeout

        def close(self, timeout):
            captured["close"] = timeout

    class FakeTracer:
        def __init__(self, **options):
            captured["tracer"] = options
            self.latest_run = SimpleNamespace(id="00000000-0000-0000-0000-000000000119")
            self.raise_error = True

    monkeypatch.setenv("TEST_LANGSMITH_KEY", "secret-never-reported")
    monkeypatch.setattr("langsmith.Client", FakeClient)
    monkeypatch.setattr("langchain_core.tracers.langchain.LangChainTracer", FakeTracer)
    observer = observability.CoordinatorObservability(
        LangSmithObservability(
            provider="langsmith",
            api_key_env="TEST_LANGSMITH_KEY",
            flush_timeout_seconds=0.5,
        )
    )
    session = observer.start(
        run_id="run-2",
        request=REQUEST,
        workflow_version="v2",
        execution_metadata={"human_review_policy": "non_blocking"},
    )
    assert session.graph_options() == {"callbacks": [session.callback]}
    assert captured["client"]["hide_inputs"] is True
    assert captured["client"]["hide_outputs"] is True
    assert captured["client"]["omit_traced_runtime_info"] is True
    assert captured["client"]["timeout_ms"] == 1000
    assert captured["client"]["retry_config"].total == 0
    assert captured["tracer"]["metadata"] == {
        "run_id": "run-2",
        "project_id": "saleor-storefront",
        "repository": "saleor/storefront",
        "pull_request": "1199",
        "workflow_version": "v2",
        "human_review_policy": "non_blocking",
    }
    assert "human_review_policy:non_blocking" in captured["tracer"]["tags"]
    annotation = session.finish()
    assert annotation["trace_id"].endswith("0119")
    assert annotation["status"] == "SUBMITTED"
    assert annotation["content_captured"] is False
    assert "secret-never-reported" not in json.dumps(annotation)
    assert captured["flush"] == 0.5


def test_cached_result_keeps_original_trace_coordinates(tmp_path, monkeypatch):
    monkeypatch.delenv("ABSENT_LANGSMITH_KEY", raising=False)
    observer = CoordinatorObservability(
        LangSmithObservability(provider="langsmith", api_key_env="ABSENT_LANGSMITH_KEY")
    )
    ledger = CallLedger(tmp_path / "calls.sqlite")
    ledger.register("run-3", "fingerprint")
    first = observer.attach(
        ledger,
        "run-3",
        {"status": "COMPLETED"},
        observer.start(run_id="run-3", request=REQUEST, workflow_version="v1"),
    )
    second = observer.attach(
        ledger,
        "run-3",
        {"status": "COMPLETED"},
        observer.start(run_id="run-3", request=REQUEST, workflow_version="v1"),
    )
    assert second["observability"] == first["observability"]
    assert len(second["observability_events"]) == 1


def test_delivery_errors_are_fail_open():
    class BrokenClient:
        def flush(self, timeout):
            raise RuntimeError("provider error with sensitive details")

        def close(self, timeout):
            raise RuntimeError("close error")

    session = TraceSession(
        annotation={
            "provider": "langsmith",
            "status": "INITIALIZED",
            "project": "demo",
            "dashboard_url": "https://smith.langchain.com",
            "trace_id": None,
            "content_captured": False,
        },
        callback=SimpleNamespace(latest_run=SimpleNamespace(id="trace-id")),
        client=BrokenClient(),
        flush_timeout_seconds=0.1,
    )
    result = session.finish()
    assert result["status"] == "SUBMITTED"
    assert result["delivery"] == "BEST_EFFORT"
    assert "sensitive" not in json.dumps(result)
