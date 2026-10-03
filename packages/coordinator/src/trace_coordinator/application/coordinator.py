"""Public run/resume API; run locks and immutable fingerprints prevent reset bypasses."""

import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Literal, cast

from filelock import FileLock
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Checkpointer, Command, Interrupt

from trace_coordinator.application.interfaces import DecisionModel, Tool
from trace_coordinator.application.runtime import ToolRegistry, ToolRuntime
from trace_coordinator.application.verification_stage import ApprovedScenario, VerificationStage
from trace_coordinator.application.workflow import WORKFLOW_VERSION, build_workflow
from trace_coordinator.config import (
    CallLimits,
    ExplorationConfig,
    HumanReviewPolicy,
    VerificationPolicy,
)
from trace_coordinator.domain.contracts import AnalysisReportPayload, JsonObject, as_json_object
from trace_coordinator.domain.errors import RunMismatch
from trace_coordinator.domain.models import AnalysisRequest, ReviewResponse, ToolContext
from trace_coordinator.domain.state import AnalysisState
from trace_coordinator.infrastructure.ledger import CallLedger, canonical, digest
from trace_coordinator.infrastructure.observability import CoordinatorObservability
from trace_coordinator.security.guardrails import GuardrailEngine, GuardrailPolicy


class Coordinator:
    def __init__(
        self,
        state_directory: Path,
        limits: CallLimits,
        tools: Sequence[Tool],
        model: DecisionModel,
        *,
        exploration: ExplorationConfig | None = None,
        verification: VerificationPolicy | None = None,
        human_review: HumanReviewPolicy | None = None,
        scenarios: Sequence[ApprovedScenario] = (),
        guardrails: GuardrailPolicy | None = None,
        observability: CoordinatorObservability | None = None,
    ) -> None:
        self.directory = Path(state_directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.limits, self.registry, self.model = limits, ToolRegistry(list(tools)), model
        self.exploration = ExplorationConfig.model_validate(exploration or {})
        self.human_review = HumanReviewPolicy.model_validate(human_review or {})
        self.verification = VerificationStage(
            VerificationPolicy.model_validate(verification or {}), scenarios, self.directory
        )
        self.guardrails = GuardrailEngine(guardrails)
        self.observability = observability or CoordinatorObservability()
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

    def _fingerprint(self, request: AnalysisRequest) -> str:
        return digest(
            {
                "workflow": WORKFLOW_VERSION,
                "request": request.model_dump(mode="json"),
                "limits": self.limits.model_dump(mode="json"),
                "exploration": self.exploration.model_dump(mode="json"),
                "verification": self.verification.fingerprint,
                "human_review": self.human_review.model_dump(mode="json"),
                "model": self.model.version,
                "guardrails": self.guardrails.version,
                "observability": self.observability.fingerprint,
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

    @staticmethod
    def _initial_state(request: AnalysisRequest) -> AnalysisState:
        return cast(
            AnalysisState,
            {
                "request": request.model_dump(mode="json"),
                "evidence": {},
                "gaps": [],
                "rounds": 0,
                "review_count": 0,
                "reviews": [],
                "review_requests": [],
                "findings": [],
            },
        )

    def run(
        self, request: AnalysisRequest, run_id: str, *, review: ReviewResponse | None = None
    ) -> JsonObject:
        request = AnalysisRequest.model_validate(request)
        self.guardrails.validate_user_text(request.question, field="request")
        if review is not None:
            self.guardrails.validate_user_text(
                ReviewResponse.model_validate(review).answer, field="human review"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
            raise ValueError("Use a safe run ID of up to 80 letters, digits, underscores or hyphens")
        fingerprint = self._fingerprint(request)
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
                    graph = build_workflow(
                        runtime,
                        context,
                        saver,
                        self.exploration,
                        self.verification,
                        self.human_review,
                    )
                    config: RunnableConfig = {
                        "configurable": {"thread_id": run_id},
                        "recursion_limit": 30 + 4 * self.limits.max_rounds,
                    }
                    trace = self.observability.start(
                        run_id=run_id,
                        request=request,
                        workflow_version=WORKFLOW_VERSION,
                        execution_metadata={
                            "run_kind": "analysis",
                            "human_review_policy": self.human_review.policy,
                            "verification_enabled": str(self.verification.policy.enabled).lower(),
                            "exploration_enabled": str(self.exploration.enabled).lower(),
                        },
                    )
                    config.update(trace.graph_options())
                    snapshot = graph.get_state(config)
                    snapshot_state = cast(AnalysisState, snapshot.values)
                    if snapshot.values and not snapshot.next:
                        if review is not None:
                            follow_up = self._follow_up_verification(
                                run_id,
                                fingerprint,
                                ReviewResponse.model_validate(review),
                                snapshot_state,
                                saver,
                                runtime,
                                context,
                                request,
                            )
                            return as_json_object(follow_up)
                        return self.observability.attach(self.ledger, run_id, snapshot_state["report"], trace)
                    pending = [i.value for task in snapshot.tasks for i in task.interrupts]
                    if pending and review is None:
                        return self.observability.attach(
                            self.ledger, run_id, self._waiting(run_id, pending), trace
                        )
                    if review is not None and not pending:
                        raise RunMismatch("This run is not waiting for human review")
                    if review is not None:
                        value: AnalysisState | Command[str] | None = Command(
                            resume=ReviewResponse.model_validate(review).model_dump(mode="json")
                        )
                    elif snapshot.values:
                        value = None
                    else:
                        value = self._initial_state(request)
                    graph_result = cast(Mapping[str, object], graph.invoke(value, config))
                    raw_interrupts = graph_result.get("__interrupt__")
                    if isinstance(raw_interrupts, (list, tuple)):
                        interrupts = cast(Sequence[Interrupt], raw_interrupts)
                        output = self._waiting(run_id, [item.value for item in interrupts])
                    else:
                        output = as_json_object(cast(AnalysisState, graph_result)["report"])
                    return self.observability.attach(self.ledger, run_id, output, trace)
                finally:
                    connection.commit()

    def _follow_up_verification(
        self,
        parent_run_id: str,
        parent_fingerprint: str,
        review: ReviewResponse,
        parent_state: AnalysisState,
        saver: Checkpointer,
        runtime: ToolRuntime,
        context: ToolContext,
        request: AnalysisRequest,
    ) -> AnalysisReportPayload:
        parent_report = parent_state["report"]
        review_section = parent_report.get("human_review", {})
        pending = [
            item
            for item in review_section.get("requests", [])
            if item.get("kind") == "verification_approval" and item.get("status") == "NOT_ANSWERED"
        ]
        if (
            self.human_review.policy != "non_blocking"
            or not self.human_review.allow_follow_up_verification
            or len(pending) != 1
        ):
            raise RunMismatch("Completed run has no unanswered verification approval eligible for follow-up")
        original = pending[0]
        follow_up_run_id = (
            "followup-" + digest({"parent_run_id": parent_run_id, "question": original["question"]})[:32]
        )
        follow_up_fingerprint = digest(
            {
                "parent_fingerprint": parent_fingerprint,
                "parent_run_id": parent_run_id,
                "question": original["question"],
                "answer": review.answer,
            }
        )
        self.ledger.register(follow_up_run_id, follow_up_fingerprint)
        outcome: Literal["APPROVED", "REJECTED"] = (
            "APPROVED" if review.answer.strip().casefold() == "approve" else "REJECTED"
        )
        link = {
            "parent_run_id": parent_run_id,
            "follow_up_run_id": follow_up_run_id,
            "question_sha256": digest(original["question"]),
            "answer_sha256": digest(review.answer),
            "outcome": outcome,
        }
        self.ledger.event_once(parent_run_id, "FOLLOW_UP_VERIFICATION_CREATED", canonical(link))
        self.ledger.event_once(parent_run_id, "HUMAN_REVIEW_RESPONSE", canonical(link))
        self.ledger.event_once(follow_up_run_id, "PARENT_RUN_LINKED", canonical(link))

        graph = build_workflow(
            runtime,
            context,
            saver,
            self.exploration,
            self.verification,
            self.human_review,
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": follow_up_run_id},
            "recursion_limit": 30 + 4 * self.limits.max_rounds,
        }
        trace = self.observability.start(
            run_id=follow_up_run_id,
            request=request,
            workflow_version=WORKFLOW_VERSION,
            execution_metadata={
                "run_kind": "follow_up_verification",
                "parent_run_id": parent_run_id,
                "human_review_outcome": outcome.lower(),
            },
        )
        config.update(trace.graph_options())
        snapshot = graph.get_state(config)
        snapshot_state = cast(AnalysisState, snapshot.values)
        if snapshot.values and not snapshot.next:
            report = snapshot_state["report"]
        else:
            if not snapshot.values:
                seed = cast(
                    AnalysisState,
                    {key: value for key, value in parent_state.items() if key != "report"},
                )
                seed.update(
                    {
                        "verification_approved": outcome == "APPROVED",
                        "reviews": [*parent_state.get("reviews", []), review.answer],
                        "review_requests": [
                            {
                                **item,
                                "status": outcome,
                                "answer": review.answer,
                            }
                            if item == original
                            else item
                            for item in parent_state.get("review_requests", [])
                        ],
                        "review_outcome": outcome,
                    }
                )
                if outcome != "APPROVED":
                    seed["verification_result"] = {
                        "status": "NOT_EXECUTED",
                        "checks": [],
                        "evidence": {},
                        "reason": "Scenario was not approved",
                    }
                graph.update_state(config, seed, as_node="verification_review")
            graph_result = cast(Mapping[str, object], graph.invoke(None, config))
            if graph_result.get("__interrupt__"):
                raise RunMismatch("Follow-up verification requested an unexpected additional review")
            report = cast(AnalysisState, graph_result)["report"]

        parent_evidence = parent_report.get("evidence", {})
        if any(report.get("evidence", {}).get(key) != value for key, value in parent_evidence.items()):
            raise RunMismatch("Follow-up verification did not preserve original evidence")
        linked = dict(report)
        linked["run_id"] = follow_up_run_id
        linked["parent_run_id"] = parent_run_id
        linked["follow_up"] = {
            **link,
            "parent_evidence_sha256": digest(parent_evidence),
            "evidence_sha256": digest(report.get("evidence", {})),
            "shared_call_ledger": parent_run_id,
            "audit_events": self.ledger.events(follow_up_run_id),
        }
        linked["human_review"] = {
            "policy": "non_blocking",
            "status": outcome,
            "requests": [
                {
                    **original,
                    "status": outcome,
                    "answer": review.answer,
                }
            ],
            "follow_up_verification_allowed": False,
        }
        if linked.get("verification") != "COMPLETED" and linked.get("status") == "COMPLETED":
            linked["status"] = "COMPLETED_WITH_GAPS"
            linked["completeness"] = "PARTIAL"
        linked["tool_usage"] = self.ledger.usage(parent_run_id)
        linked["audit_events"] = self.ledger.events(parent_run_id)
        return cast(
            AnalysisReportPayload,
            self.observability.attach(self.ledger, follow_up_run_id, linked, trace),
        )

    def _waiting(self, run_id: str, pending: list[object]) -> JsonObject:
        return as_json_object(
            {
                "run_id": run_id,
                "status": "WAITING_FOR_REVIEW",
                "questions": pending,
                "tool_usage": self.ledger.usage(run_id),
            }
        )
