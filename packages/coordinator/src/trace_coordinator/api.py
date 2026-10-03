"""Public run/resume API; run locks and immutable fingerprints prevent reset bypasses."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from filelock import FileLock
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from trace_coordinator.config import (
    CallLimits,
    ExplorationConfig,
    HumanReviewPolicy,
    VerificationPolicy,
)
from trace_coordinator.errors import RunMismatch
from trace_coordinator.guardrails import GuardrailEngine
from trace_coordinator.ledger import CallLedger, canonical, digest
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
        human_review=None,
        scenarios=(),
        guardrails=None,
    ):
        self.directory = Path(state_directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.limits, self.registry, self.model = limits, ToolRegistry(tools), model
        self.exploration = ExplorationConfig.model_validate(exploration or {})
        self.human_review = HumanReviewPolicy.model_validate(human_review or {})
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

    def _fingerprint(self, request):
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
    def _initial_state(request):
        return {
            "request": request.model_dump(mode="json"),
            "evidence": {},
            "gaps": [],
            "rounds": 0,
            "review_count": 0,
            "reviews": [],
            "review_requests": [],
            "findings": [],
        }

    def run(self, request: AnalysisRequest, run_id: str, *, review: ReviewResponse | None = None):
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
                    config = {
                        "configurable": {"thread_id": run_id},
                        "recursion_limit": 30 + 4 * self.limits.max_rounds,
                    }
                    snapshot = graph.get_state(config)
                    if snapshot.values and not snapshot.next:
                        if review is not None:
                            return self._follow_up_verification(
                                run_id,
                                fingerprint,
                                ReviewResponse.model_validate(review),
                                snapshot.values,
                                saver,
                                runtime,
                                context,
                            )
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
                        value = self._initial_state(request)
                    result = graph.invoke(value, config)
                    if result.get("__interrupt__"):
                        return self._waiting(run_id, [item.value for item in result["__interrupt__"]])
                    return result["report"]
                finally:
                    connection.commit()

    def _follow_up_verification(
        self, parent_run_id, parent_fingerprint, review, parent_state, saver, runtime, context
    ):
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
        outcome = "APPROVED" if review.answer.strip().casefold() == "approve" else "REJECTED"
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
        config = {
            "configurable": {"thread_id": follow_up_run_id},
            "recursion_limit": 30 + 4 * self.limits.max_rounds,
        }
        snapshot = graph.get_state(config)
        if snapshot.values and not snapshot.next:
            report = snapshot.values["report"]
        else:
            if not snapshot.values:
                seed = {key: value for key, value in parent_state.items() if key != "report"}
                seed.update(
                    {
                        "verification_approved": outcome == "APPROVED",
                        "reviews": [*seed.get("reviews", []), review.answer],
                        "review_requests": [
                            {
                                **item,
                                "status": outcome,
                                "answer": review.answer,
                            }
                            if item == original
                            else item
                            for item in seed.get("review_requests", [])
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
            result = graph.invoke(None, config)
            if result.get("__interrupt__"):
                raise RunMismatch("Follow-up verification requested an unexpected additional review")
            report = result["report"]

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
        return linked

    def _waiting(self, run_id, pending):
        return {
            "run_id": run_id,
            "status": "WAITING_FOR_REVIEW",
            "questions": pending,
            "tool_usage": self.ledger.usage(run_id),
        }
