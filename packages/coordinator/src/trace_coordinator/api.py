"""Public run/resume API; run locks and immutable fingerprints prevent reset bypasses."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from filelock import FileLock
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from trace_coordinator.config import CallLimits, ExplorationConfig, VerificationPolicy
from trace_coordinator.errors import RunMismatch
from trace_coordinator.guardrails import GuardrailEngine
from trace_coordinator.ledger import CallLedger, digest
from trace_coordinator.models import AnalysisRequest, ReviewResponse, ToolContext
from trace_coordinator.runtime import ToolRegistry, ToolRuntime
from trace_coordinator.verification_stage import VerificationStage
from trace_coordinator.workflow import WORKFLOW_VERSION, build_workflow


class Coordinator:
    def __init__(
        self,
        state_directory: Path,
        limits: CallLimits,
        tools,
        model,
        *,
        exploration=None,
        verification=None,
        scenarios=(),
        guardrails=None,
    ):
        self.directory = Path(state_directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.limits, self.registry, self.model = limits, ToolRegistry(tools), model
        self.exploration = ExplorationConfig.model_validate(exploration or {})
        self.verification = VerificationStage(
            VerificationPolicy.model_validate(verification or {}), scenarios, self.directory
        )
        self.guardrails = GuardrailEngine(guardrails)
        for agent, overrides in limits.overrides.items():
            # Foundation has one stable agent; worker-generated names cannot reset quotas.
            approved_tools = {name for scenario in scenarios for name in scenario.required_calls}
            if agent != "coordinator" or not set(overrides) <= {
                *self.registry.tools,
                *approved_tools,
                "model.decide",
            }:
                raise ValueError("Override refers to an unknown agent or canonical tool")
        self.ledger = CallLedger(self.directory / "calls.sqlite")

    def run(self, request: AnalysisRequest, run_id: str, *, review: ReviewResponse | None = None):
        request = AnalysisRequest.model_validate(request)
        self.guardrails.validate_user_text(request.question, field="request")
        if review is not None:
            self.guardrails.validate_user_text(
                ReviewResponse.model_validate(review).answer, field="human review"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
            raise ValueError("Use a safe run ID of up to 80 letters, digits, underscores or hyphens")
        fingerprint = digest(
            {
                "workflow": WORKFLOW_VERSION,
                "request": request.model_dump(mode="json"),
                "limits": self.limits.model_dump(mode="json"),
                "exploration": self.exploration.model_dump(mode="json"),
                "verification": self.verification.fingerprint,
                "model": self.model.version,
                "guardrails": self.guardrails.version,
                "tools": {
                    name: {
                        "version": tool.version,
                        "agents": sorted(tool.allowed_agents),
                        "schema": tool.input_model.model_json_schema(),
                    }
                    for name, tool in sorted(self.registry.tools.items())
                },
            }
        )
        with FileLock(str(self.directory / f"{run_id}.lock"), timeout=0):
            self.ledger.register(run_id, fingerprint)
            with closing(
                sqlite3.connect(self.directory / "checkpoints.sqlite", check_same_thread=False)
            ) as connection:
                try:
                    saver = SqliteSaver(connection)
                    context = ToolContext(
                        run_id=run_id, project_id=request.project_id, agent_id="coordinator"
                    )
                    runtime = ToolRuntime(
                        self.ledger,
                        self.limits,
                        self.registry,
                        self.model,
                        self.guardrails,
                    )
                    graph = build_workflow(runtime, context, saver, self.exploration, self.verification)
                    config = {
                        "configurable": {"thread_id": run_id},
                        "recursion_limit": 30 + 4 * self.limits.max_rounds,
                    }
                    snapshot = graph.get_state(config)
                    if snapshot.values and not snapshot.next:
                        if review is not None:
                            raise RunMismatch("Completed runs do not accept review responses")
                        return snapshot.values["report"]
                    pending = [i.value for task in snapshot.tasks for i in task.interrupts]
                    if pending and review is None:
                        return self._waiting(run_id, pending)
                    if review is not None and not pending:
                        raise RunMismatch("This run is not waiting for human review")
                    if review is not None:
                        value = Command(resume=ReviewResponse.model_validate(review).model_dump(mode="json"))
                    elif snapshot.values:
                        value = None
                    else:
                        value = {
                            "request": request.model_dump(mode="json"),
                            "evidence": {},
                            "gaps": [],
                            "rounds": 0,
                            "review_count": 0,
                            "reviews": [],
                            "findings": [],
                        }
                    result = graph.invoke(value, config)
                    if result.get("__interrupt__"):
                        return self._waiting(run_id, [item.value for item in result["__interrupt__"]])
                    return result["report"]
                finally:
                    connection.commit()

    def _waiting(self, run_id, pending):
        return {
            "run_id": run_id,
            "status": "WAITING_FOR_REVIEW",
            "questions": pending,
            "tool_usage": self.ledger.usage(run_id),
        }
