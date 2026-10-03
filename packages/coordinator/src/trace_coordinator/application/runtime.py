"""The single dispatch boundary for tools AND decision-model calls."""

import json
import time

from trace_coordinator.application.interfaces import DecisionModel, Tool
from trace_coordinator.config import CallLimits
from trace_coordinator.domain.errors import FailureCode, ToolFailure
from trace_coordinator.domain.models import Decision, ToolContext, ToolResult
from trace_coordinator.infrastructure.ledger import CallLedger
from trace_coordinator.security.guardrails import GuardrailEngine


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self.tools = {}
        for tool in tools:
            if tool.name in self.tools or tool.name == "model.decide":
                raise ValueError("Duplicate or reserved canonical tool name")
            self.tools[tool.name] = tool

    def descriptions(self, agent):
        return [
            {"name": t.name, "description": t.description, "arguments": t.input_model.model_json_schema()}
            for t in self.tools.values()
            if agent in t.allowed_agents
        ]


class ToolRuntime:
    def __init__(
        self,
        ledger: CallLedger,
        limits: CallLimits,
        registry: ToolRegistry,
        model: DecisionModel,
        guardrails=None,
    ):
        self.ledger, self.limits, self.registry, self.model = ledger, limits, registry, model
        self.guardrails = guardrails or GuardrailEngine()

    def _execute(self, context, operation, name, payload, callback, result_type):
        for attempt in range(1, self.limits.retry_attempts + 1):
            prior = self.ledger.reserve(
                context.run_id, operation, attempt, context.agent_id, name, payload, self.limits
            )
            if prior:
                if prior["status"] == "SUCCEEDED":
                    return result_type.model_validate_json(prior["result"])
                failure = ToolFailure(
                    json.loads(prior["result"])["error"], retryable=bool(prior["retryable"])
                )
            else:
                try:
                    result = result_type.model_validate(callback())
                except Exception as exc:
                    # Provider exception bodies can contain credentials or sensitive input.
                    retryable = isinstance(exc, ToolFailure) and exc.retryable
                    code = exc.code if isinstance(exc, ToolFailure) else FailureCode.UNSPECIFIED
                    category = code.value if code != FailureCode.UNSPECIFIED else type(exc).__name__
                    failure = ToolFailure(f"{name} failed ({category})", retryable=retryable, code=code)
                    self.ledger.finish(
                        context.run_id, operation, attempt, "FAILED", {"error": str(failure)}, retryable
                    )
                else:
                    # If saving fails after a successful side effect, leave STARTED intact.
                    # Recovery must treat that execution as uncertain, not retry the callback.
                    self.ledger.finish(
                        context.run_id, operation, attempt, "SUCCEEDED", result.model_dump(mode="json")
                    )
                    return result
            if not failure.retryable or attempt == self.limits.retry_attempts:
                raise failure
            time.sleep(self.limits.retry_delay_seconds)
        raise AssertionError("Attempt count must be positive")

    def call_tool(self, context: ToolContext, operation: str, name: str, arguments: dict) -> ToolResult:
        tool = self.registry.tools.get(name)
        if tool is None or context.agent_id not in tool.allowed_agents:
            raise ToolFailure("Unknown or unauthorized tool")
        try:
            parsed = tool.input_model.model_validate(arguments)
        except ValueError as exc:
            raise ToolFailure("Invalid tool arguments") from exc

        def execute():
            result = ToolResult.model_validate(tool.execute(parsed, context))
            if any(e.project_id != context.project_id for e in result.evidence):
                raise ToolFailure("Out-of-scope tool evidence")
            if len({e.id for e in result.evidence}) != len(result.evidence):
                raise ToolFailure("Duplicate evidence identity")
            return result

        return self._execute(context, operation, name, parsed.model_dump(mode="json"), execute, ToolResult)

    def decide(self, context: ToolContext, operation: str, payload: dict) -> Decision:
        safe_payload, input_audit = self.guardrails.prepare_model_input(payload)
        self.ledger.event_once(
            context.run_id,
            "MODEL_GUARDRAIL",
            json.dumps(
                {"operation": operation, "stage": "input", **input_audit},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

        def decide():
            decision, output_audit = self.guardrails.validate_model_output(self.model.decide(safe_payload))
            self.ledger.event_once(
                context.run_id,
                "MODEL_GUARDRAIL",
                json.dumps(
                    {"operation": operation, "stage": "output", **output_audit},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            return decision

        return self._execute(context, operation, "model.decide", safe_payload, decide, Decision)
