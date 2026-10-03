from concurrent.futures import ThreadPoolExecutor

import pytest

from trace_coordinator.adapters.fixtures import FixtureModel, QueryInput
from trace_coordinator.config import CallLimits
from trace_coordinator.errors import LimitReached, RunMismatch, ToolFailure, UncertainExecution
from trace_coordinator.ledger import CallLedger
from trace_coordinator.models import Evidence, ToolContext, ToolResult
from trace_coordinator.runtime import ToolRegistry, ToolRuntime


class CountingTool:
    name = "search"
    version = "test-v1"
    description = "Test search"
    input_model = QueryInput
    allowed_agents = frozenset({"coordinator", "browser"})

    def __init__(self, failure=False):
        self.calls = 0
        self.failure = failure

    def execute(self, arguments, context):
        self.calls += 1
        if self.failure:
            raise ToolFailure("Provider raw message must not leak", retryable=True)
        return ToolResult()


def make_runtime(tmp_path, limits=None, tool=None):
    ledger = CallLedger(tmp_path / "calls.sqlite")
    ledger.register("run", "fixed")
    tool = tool or CountingTool()
    runtime = ToolRuntime(
        ledger,
        limits or CallLimits(retry_delay_seconds=0),
        ToolRegistry([tool]),
        FixtureModel([{"action": "finish"}]),
    )
    context = ToolContext(run_id="run", project_id="project", agent_id="coordinator")
    return runtime, context, tool


def test_sixth_call_never_executes_and_denial_is_recorded(tmp_path):
    runtime, context, tool = make_runtime(tmp_path)
    for i in range(5):
        runtime.call_tool(context, str(i), "search", {"query": str(i)})
    with pytest.raises(LimitReached, match="5-attempt"):
        runtime.call_tool(context, "sixth", "search", {"query": "different argument"})
    assert tool.calls == 5
    assert runtime.ledger.usage("run")[0]["attempts"] == 5
    assert runtime.ledger.events("run")[0]["kind"] == "LIMIT_REACHED"


def test_successful_checkpoint_replay_is_cached_not_reexecuted(tmp_path):
    runtime, context, tool = make_runtime(tmp_path)
    runtime.call_tool(context, "op", "search", {"query": "same"})
    runtime.call_tool(context, "op", "search", {"query": "same"})
    assert tool.calls == 1
    assert runtime.ledger.usage("run")[0]["attempts"] == 1


def test_changing_arguments_cannot_reuse_operation_id(tmp_path):
    runtime, context, _ = make_runtime(tmp_path)
    runtime.call_tool(context, "op", "search", {"query": "one"})
    with pytest.raises(RunMismatch):
        runtime.call_tool(context, "op", "search", {"query": "two"})


def test_retries_and_failures_consume_cap(tmp_path):
    runtime, context, tool = make_runtime(
        tmp_path, CallLimits(retry_attempts=3, retry_delay_seconds=0), CountingTool(True)
    )
    with pytest.raises(ToolFailure):
        runtime.call_tool(context, "first", "search", {"query": "one"})
    with pytest.raises(LimitReached):
        runtime.call_tool(context, "second", "search", {"query": "two"})
    assert tool.calls == 5
    assert runtime.ledger.usage("run")[0]["failed"] == 5
    with runtime.ledger.connect() as db:
        saved = " ".join(row[0] for row in db.execute("SELECT result FROM calls"))
    assert "Provider raw message" not in saved


def test_replaying_failure_does_not_add_attempts(tmp_path):
    runtime, context, tool = make_runtime(tmp_path, CallLimits(retry_delay_seconds=0), CountingTool(True))
    for _ in range(2):
        with pytest.raises(ToolFailure):
            runtime.call_tool(context, "op", "search", {"query": "same"})
    assert tool.calls == 2


def test_new_runtime_does_not_reset_counters(tmp_path):
    runtime, context, _ = make_runtime(tmp_path)
    for i in range(5):
        runtime.call_tool(context, str(i), "search", {"query": str(i)})
    restarted, context, tool = make_runtime(tmp_path)
    with pytest.raises(LimitReached):
        restarted.call_tool(context, "new", "search", {"query": "new"})
    assert tool.calls == 0


def test_crash_after_reservation_is_not_automatically_replayed(tmp_path):
    runtime, context, tool = make_runtime(tmp_path)
    runtime.ledger.reserve("run", "op", 1, "coordinator", "search", {"query": "same"}, runtime.limits)
    with pytest.raises(UncertainExecution):
        runtime.call_tool(context, "op", "search", {"query": "same"})
    assert tool.calls == 0
    assert runtime.ledger.usage("run")[0]["uncertain"] == 1


def test_concurrent_reservations_are_atomic(tmp_path):
    runtime, _, _ = make_runtime(tmp_path)

    def reserve(i):
        try:
            runtime.ledger.reserve("run", str(i), 1, "coordinator", "search", {}, runtime.limits)
            return True
        except LimitReached:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(reserve, range(25))) == 5
    assert runtime.ledger.usage("run")[0]["attempts"] == 5


def test_per_agent_counts_and_global_limit(tmp_path):
    runtime, context, tool = make_runtime(tmp_path, CallLimits(total_calls=6))
    for i in range(5):
        runtime.call_tool(context, str(i), "search", {"query": "q"})
    browser = context.model_copy(update={"agent_id": "browser"})
    runtime.call_tool(browser, "browser-1", "search", {"query": "q"})
    with pytest.raises(LimitReached, match="total"):
        runtime.call_tool(browser, "browser-2", "search", {"query": "q"})
    assert tool.calls == 6


def test_lower_override(tmp_path):
    runtime, context, tool = make_runtime(tmp_path, CallLimits(overrides={"coordinator": {"search": 1}}))
    runtime.call_tool(context, "first", "search", {"query": "q"})
    with pytest.raises(LimitReached, match="1-attempt"):
        runtime.call_tool(context, "second", "search", {"query": "q"})
    assert tool.calls == 1


def test_model_calls_use_same_guard(tmp_path):
    runtime, context, _ = make_runtime(tmp_path)
    for i in range(5):
        runtime.decide(context, str(i), {"round": i + 1})
    with pytest.raises(LimitReached):
        runtime.decide(context, "sixth", {"round": 6})


def test_deadline_blocks_new_execution(tmp_path):
    runtime, context, tool = make_runtime(tmp_path)
    with runtime.ledger.connect() as db:
        db.execute("UPDATE runs SET started=0")
    with pytest.raises(LimitReached, match="deadline"):
        runtime.call_tool(context, "op", "search", {"query": "q"})
    assert tool.calls == 0


@pytest.mark.parametrize("name,args", [("other", {"query": "q"}), ("search", {"wrong": "q"})])
def test_invalid_dispatch_does_not_execute(tmp_path, name, args):
    runtime, context, tool = make_runtime(tmp_path)
    with pytest.raises(ToolFailure):
        runtime.call_tool(context, "op", name, args)
    assert tool.calls == 0


def test_out_of_scope_results_are_rejected_and_counted(tmp_path):
    class LeakingTool(CountingTool):
        def execute(self, arguments, context):
            return ToolResult(
                evidence=(
                    Evidence(id="x", project_id="other", kind="document", summary="x", source="fixture"),
                )
            )

    runtime, context, _ = make_runtime(tmp_path, tool=LeakingTool())
    with pytest.raises(ToolFailure):
        runtime.call_tool(context, "op", "search", {"query": "q"})
    assert runtime.ledger.usage("run")[0]["failed"] == 1


def test_registry_rejects_alias_collision(tmp_path):
    with pytest.raises(ValueError):
        ToolRegistry([CountingTool(), CountingTool()])


def test_register_rejects_different_config(tmp_path):
    runtime, _, _ = make_runtime(tmp_path)
    with pytest.raises(RunMismatch):
        runtime.ledger.register("run", "changed")


def test_ledger_write_failure_after_action_is_uncertain_not_retried(tmp_path, monkeypatch):
    runtime, context, tool = make_runtime(tmp_path)

    def fail_save(*args, **kwargs):
        raise OSError("Simulated disk failure")

    monkeypatch.setattr(runtime.ledger, "finish", fail_save)
    with pytest.raises(OSError):
        runtime.call_tool(context, "op", "search", {"query": "q"})
    restarted, _, _ = make_runtime(tmp_path)
    with pytest.raises(UncertainExecution):
        restarted.call_tool(context, "op", "search", {"query": "q"})
    assert tool.calls == 1
