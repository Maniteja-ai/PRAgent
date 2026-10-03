"""Explicit orchestration with a bounded tool-using reasoning loop."""

import json
from collections.abc import Callable
from typing import cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer, interrupt
from pydantic import ValidationError

from trace_coordinator.application.exploration import exploration_status, screens
from trace_coordinator.application.runtime import ToolRuntime
from trace_coordinator.application.ui_evidence import ui_index
from trace_coordinator.application.verification_stage import VerificationStage, skipped
from trace_coordinator.config import ExplorationConfig, HumanReviewPolicy
from trace_coordinator.domain.contracts import (
    AnalysisReportPayload,
    DecisionPayload,
    ExplorationContextPayload,
    FindingPayload,
    JsonObject,
    ModelContextPayload,
    ReviewRequestPayload,
    RuntimeAttestationPayload,
)
from trace_coordinator.domain.errors import LimitReached, ToolFailure, UncertainExecution
from trace_coordinator.domain.models import ChangeSet, Decision, Evidence, ReviewResponse, ToolContext
from trace_coordinator.domain.state import AnalysisState, merge_evidence
from trace_coordinator.infrastructure.ledger import canonical, digest

WORKFLOW_VERSION = "pr-impact-production-v5"


def build_workflow(
    runtime: ToolRuntime,
    context: ToolContext,
    checkpointer: Checkpointer,
    exploration: ExplorationConfig,
    verification: VerificationStage,
    human_review_policy: HumanReviewPolicy,
) -> CompiledStateGraph[AnalysisState, None, AnalysisState, AnalysisState]:
    limits = runtime.limits

    def stopped(state: AnalysisState, exc: Exception) -> AnalysisState:
        return {"gaps": [*state.get("gaps", []), str(exc)], "stop_reason": type(exc).__name__}

    def scoped_context(state: AnalysisState) -> ToolContext:
        changes = next(
            (
                e["metadata"]["changes"]
                for e in state.get("evidence", {}).values()
                if e["kind"] == "diff" and "changes" in e["metadata"]
            ),
            None,
        )
        attestations = {
            e["metadata"]["environment"]: e["metadata"]
            for e in state.get("evidence", {}).values()
            if e["kind"] == "attestation"
        }
        return context.model_copy(
            update={
                "changes": ChangeSet.model_validate(changes) if changes else None,
                "runtime_attestations": attestations,
            }
        )

    def fetch_changes(state: AnalysisState) -> AnalysisState:
        request = state["request"]
        try:
            result = runtime.call_tool(
                context,
                "fetch_changes",
                "github.diff",
                {
                    "repository": request["repository"],
                    "pull_request": request["pull_request"],
                },
            )
            if not any(e.kind == "diff" for e in result.evidence):
                raise ToolFailure("Diff tool did not provide change evidence")
            return {"evidence": merge_evidence({}, result.evidence), "gaps": list(result.gaps)}
        except (LimitReached, ToolFailure, UncertainExecution) as exc:
            return stopped(state, exc)

    def retrieve(state: AnalysisState) -> AnalysisState:
        evidence, gaps = dict(state["evidence"]), list(state.get("gaps", []))
        for name in ("knowledge.graph", "knowledge.documents"):
            try:
                result = runtime.call_tool(
                    scoped_context(state),
                    f"initial:{name}",
                    name,
                    {
                        "query": state["request"]["question"],
                    },
                )
                evidence = merge_evidence(evidence, result.evidence)
                gaps.extend(result.gaps)
            except ToolFailure as exc:
                gaps.append(str(exc))
            except (LimitReached, UncertainExecution) as exc:
                return {"evidence": evidence, "gaps": [*gaps, str(exc)], "stop_reason": type(exc).__name__}
        return {"evidence": evidence, "gaps": gaps}

    def attest_deployments(state: AnalysisState) -> AnalysisState:
        if "deployment.attest" not in runtime.registry.tools:
            return {"usage": runtime.ledger.usage(context.run_id)}
        evidence, gaps = dict(state["evidence"]), list(state.get("gaps", []))
        for environment in ("baseline", "patched"):
            try:
                result = runtime.call_tool(
                    context,
                    f"attest:{environment}",
                    "deployment.attest",
                    {"environment": environment},
                )
                if len(result.evidence) != 1 or result.evidence[0].kind != "attestation":
                    raise ToolFailure("Deployment attestation did not return one attestation record")
                evidence = merge_evidence(evidence, result.evidence)
            except (LimitReached, ToolFailure, UncertainExecution) as exc:
                return {
                    "evidence": evidence,
                    "gaps": [*gaps, str(exc)],
                    "stop_reason": type(exc).__name__,
                }
        records = [e for e in evidence.values() if e["kind"] == "attestation"]
        if len(records) != 2 or len({e["metadata"]["backend_fingerprint"] for e in records}) != 1:
            return {
                "evidence": evidence,
                "gaps": [*gaps, "Baseline and patched deployments do not attest the same backend"],
                "stop_reason": "ToolFailure",
            }
        return {"evidence": evidence, "gaps": gaps, "usage": runtime.ledger.usage(context.run_id)}

    def reason(state: AnalysisState) -> AnalysisState:
        if state["rounds"] >= limits.max_rounds:
            return stopped(state, LimitReached("Reasoning round limit reached"))
        next_round = state["rounds"] + 1
        exploration_context: ExplorationContextPayload = {
            "enabled": exploration.enabled,
            "environment": exploration.environment,
            "goal": exploration.goal,
            "target_controls": list(exploration.target_controls),
            "max_steps": exploration.max_steps,
            "max_state_visits": exploration.max_state_visits,
            "status": state.get("exploration_status", "DISABLED"),
            "steps": state.get("exploration_steps", 0),
        }
        payload: ModelContextPayload = {
            "request": state["request"],
            "round": next_round,
            "evidence": state["evidence"],
            "gaps": state["gaps"],
            "human_answers": state["reviews"],
            "tools": [
                t for t in runtime.registry.descriptions(context.agent_id) if t["name"] != "github.diff"
            ],
            # Persisted attempts, rather than model-supplied counters, are authoritative.
            # Use the checkpointed usage snapshot so crash replay inputs stay identical.
            "per_tool_limit": limits.per_agent_tool,
            "calls_already_used": state.get("usage", []),
            "validation_errors": state.get("validation_errors", []),
            "previous_findings": state.get("decision", {}).get("findings", []),
            "phase": state.get("phase", "analysis"),
            "exploration": exploration_context,
        }
        if verification.policy.enabled and state.get("phase") != "exploration":
            payload["tools"] = [
                t
                for t in payload["tools"]
                if t["name"] not in {"browser.act", "browser.navigate", "browser.check"}
                and not t["name"].startswith("fixture.")
            ]
            payload["approved_verification"] = {
                "selection": "After valid findings, the workflow selects exactly one configured scenario by changed paths. Do not execute its steps yourself.",
                "scenarios": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "changed_paths": list(s.changed_paths),
                    }
                    for s in verification.scenarios.values()
                ],
            }
        if state.get("phase") == "exploration":
            # The explorer needs the current screen, not stale controls from every
            # earlier capture. Full evidence remains checkpointed for analysis.
            current_id, _ = screens(state["evidence"], exploration.environment)[-1]
            payload["evidence"] = {current_id: state["evidence"][current_id]}
            payload["latest_snapshot_id"] = current_id
            payload["tools"] = [t for t in payload["tools"] if t["name"].startswith("browser.")]
            payload["previous_findings"] = []
        try:
            decision = runtime.decide(context, f"reason:{next_round}", payload)
            return {
                "decision": cast(DecisionPayload, decision.model_dump(mode="json")),
                "rounds": next_round,
            }
        except (LimitReached, ToolFailure, UncertainExecution) as exc:
            return {**stopped(state, exc), "rounds": next_round}

    def observe_entries(state: AnalysisState) -> AnalysisState:
        if "browser.navigate" not in runtime.registry.tools:
            return {"usage": runtime.ledger.usage(context.run_id)}
        evidence, gaps = dict(state["evidence"]), list(state["gaps"])
        for environment in ("baseline", "patched"):
            try:
                result = runtime.call_tool(
                    scoped_context(state),
                    f"entry:{environment}",
                    "browser.navigate",
                    {"environment": environment, "path": ""},
                )
                evidence = merge_evidence(evidence, result.evidence)
                gaps.extend(result.gaps)
            except ToolFailure as exc:
                gaps.append(str(exc))
            except (LimitReached, UncertainExecution) as exc:
                return {"evidence": evidence, "gaps": [*gaps, str(exc)], "stop_reason": type(exc).__name__}
        return {"evidence": evidence, "gaps": gaps, "usage": runtime.ledger.usage(context.run_id)}

    def execute_tool(state: AnalysisState) -> AnalysisState:
        decision = Decision.model_validate(state["decision"])
        try:
            tool_name = decision.tool
            if tool_name is None:
                raise ToolFailure("Tool decision is missing its canonical tool name")
            if tool_name == "github.diff":
                raise ToolFailure("The pinned PR cannot be replaced during reasoning")
            if verification.policy.enabled and (
                tool_name.startswith("fixture.")
                or tool_name == "browser.check"
                or (state.get("phase") != "exploration" and tool_name in {"browser.act", "browser.navigate"})
            ):
                raise ToolFailure("Verification actions are reserved for the approved scenario stage")
            if exploration.enabled and state.get("phase") == "analysis" and tool_name.startswith("browser."):
                raise ToolFailure("Browser exploration has ended; report its recorded scope and gaps")
            if state.get("phase") == "exploration" and (
                tool_name not in {"browser.navigate", "browser.observe", "browser.act"}
                or decision.arguments.get("environment") != exploration.environment
            ):
                raise ToolFailure("Exploration requires a browser tool in its configured environment")
            result = runtime.call_tool(
                scoped_context(state), f"tool:{state['rounds']}", tool_name, decision.arguments
            )
            return {
                "evidence": merge_evidence(state["evidence"], result.evidence),
                "gaps": [*state["gaps"], *result.gaps],
                "usage": runtime.ledger.usage(context.run_id),
                "exploration_steps": state.get("exploration_steps", 0)
                + (state.get("phase") == "exploration"),
            }
        except ToolFailure as exc:
            return {
                "gaps": [*state["gaps"], str(exc)],
                "usage": runtime.ledger.usage(context.run_id),
                "exploration_steps": state.get("exploration_steps", 0)
                + (state.get("phase") == "exploration"),
            }
        except (LimitReached, UncertainExecution) as exc:
            return stopped(state, exc)

    def validate_findings(state: AnalysisState) -> AnalysisState:
        decision = Decision.model_validate(state["decision"])
        findings: list[FindingPayload] = []
        gaps = list(state["gaps"])
        errors: list[str] = []
        aliases: dict[str, set[str]] = {}
        for evidence_id, item in state["evidence"].items():
            if item["kind"] != "graph":
                continue
            try:
                sections = json.loads(item["summary"])
            except (TypeError, ValueError):
                continue
            for section in sections if isinstance(sections, list) else ():
                for key in ("ui_ids", "flow_ids", "requirement_ids"):
                    for nested_id in section.get(key, []):
                        aliases.setdefault(nested_id, set()).add(evidence_id)
                for entity in section.get("mapped_entities", []):
                    if isinstance(entity, dict) and entity.get("id"):
                        aliases.setdefault(entity["id"], set()).add(evidence_id)
        for finding in decision.findings:
            normalized, invalid = [], []
            for ref in finding.evidence_ids:
                if ref in state["evidence"]:
                    normalized.append(ref)
                elif len(aliases.get(ref, ())) == 1:
                    normalized.extend(aliases[ref])
                else:
                    invalid.append(ref)
            normalized = list(dict.fromkeys(normalized))
            if invalid:
                errors.append(
                    f"Rejected unsupported finding: {finding.title}; unknown IDs: {invalid}. Copy exact evidence IDs, including every suffix."
                )
            elif not any(state["evidence"][ref]["kind"] != "diff" for ref in normalized):
                errors.append(f"Finding has no independent supporting evidence: {finding.title}")
            else:
                findings.append(
                    cast(
                        FindingPayload,
                        finding.model_copy(update={"evidence_ids": tuple(normalized)}).model_dump(
                            mode="json"
                        ),
                    )
                )
        if errors and state.get("validation_repairs", 0) < limits.max_validation_repairs:
            return {
                "findings": findings,
                "validation_errors": errors,
                "validation_repairs": state.get("validation_repairs", 0) + 1,
                "usage": runtime.ledger.usage(context.run_id),
            }
        gaps.extend(errors)
        if not findings:
            gaps.append("No sufficiently referenced impact findings; this does not establish zero impact")
        return {"findings": findings, "gaps": gaps, "stop_reason": "ANALYSIS_FINISHED"}

    def human_review(state: AnalysisState) -> AnalysisState:
        if state["review_count"] >= limits.max_review_requests:
            return stopped(state, LimitReached("Human review request limit reached"))
        question = state["decision"]["question"] or "Review the current evidence before continuing."
        if human_review_policy.policy == "non_blocking":
            review_request: ReviewRequestPayload = {
                "kind": "analysis",
                "question": question,
                "status": "NOT_ANSWERED",
                "answer": None,
            }
            return {
                "review_requests": [
                    *state.get("review_requests", []),
                    review_request,
                ],
                "review_count": state["review_count"] + 1,
                "review_outcome": "NOT_ANSWERED",
                "gaps": [
                    *state.get("gaps", []),
                    "Human clarification was requested but not answered; analysis finalized at the current evidence boundary.",
                ],
                "stop_reason": "ANALYSIS_FINISHED",
                "usage": runtime.ledger.usage(context.run_id),
            }
        # This dedicated node performs no side effects before interrupt.
        response = interrupt(
            {
                "question": question,
                "run_id": context.run_id,
                "instruction": "Resume with an answer; limits are not reset.",
            }
        )
        try:
            answer = ReviewResponse.model_validate(response)
        except ValidationError:
            return stopped(state, ToolFailure("Invalid human review response"))
        answered_request: ReviewRequestPayload = {
            "kind": "analysis",
            "question": question,
            "status": "ANSWERED",
            "answer": answer.answer,
        }
        return {
            "reviews": [*state["reviews"], answer.answer],
            "review_requests": [
                *state.get("review_requests", []),
                answered_request,
            ],
            "review_count": state["review_count"] + 1,
            "review_outcome": "ANSWERED",
            "usage": runtime.ledger.usage(context.run_id),
        }

    def finalize(state: AnalysisState) -> AnalysisState:
        stop_reason = state.get("stop_reason", "FINISHED")
        status = (
            "COMPLETED"
            if stop_reason == "ANALYSIS_FINISHED"
            else "STOPPED"
            if stop_reason == "LimitReached"
            else "FAILED"
        )
        if status == "COMPLETED" and state.get("review_outcome") == "NOT_ANSWERED":
            status = "COMPLETED_WITH_GAPS"
        pending_verification = next(
            (
                item
                for item in state.get("review_requests", [])
                if item.get("kind") == "verification_approval" and item.get("status") == "NOT_ANSWERED"
            ),
            None,
        )
        if pending_verification and human_review_policy.allow_follow_up_verification:
            runtime.ledger.event_once(
                context.run_id,
                "REVIEW_WINDOW_OPENED",
                canonical(
                    {
                        "question_sha256": digest(pending_verification["question"]),
                        "scenario_id": pending_verification["scenario_id"],
                    }
                ),
            )
        gaps = list(dict.fromkeys(state.get("gaps", [])))
        if exploration.enabled:
            gaps.append(
                "Browser exploration: "
                + state.get("exploration_status", "NOT_EXPLORED")
                + "; observed controls do not establish a passing behavioral test."
            )
        behavior = state.get("verification_result", skipped("Analysis did not reach verification"))
        attested: dict[str, JsonObject] = {}
        for evidence in state.get("evidence", {}).values():
            environment = evidence["metadata"].get("environment")
            if evidence["kind"] == "attestation" and isinstance(environment, str):
                attested[environment] = evidence["metadata"]
        backend_fingerprints = {
            fingerprint
            for item in attested.values()
            if isinstance((fingerprint := item.get("backend_fingerprint")), str)
        }
        runtime_attestation: RuntimeAttestationPayload = {
            "status": "VERIFIED" if set(attested) == {"baseline", "patched"} else "NOT_VERIFIED",
            "deployments": attested,
            "shared_backend": len(attested) == 2 and len(backend_fingerprints) == 1,
        }
        guardrail_events = [
            event for event in runtime.ledger.events(context.run_id) if event["kind"] == "MODEL_GUARDRAIL"
        ]
        if runtime_attestation["status"] == "VERIFIED":
            gaps = [
                gap
                for gap in gaps
                if gap
                != "Deployed source patch is verified; the current URL's build identity still requires runtime attestation."
            ]
        if behavior["status"] == "COMPLETED":
            gaps = [
                g
                for g in gaps
                if g
                != "Browser captures establish observed UI only; no automated before/after assertion was made."
            ]
            gaps.append(
                "Only the explicitly listed behavioral checks were run; unlisted flows are outside scope."
            )
            requirements = behavior.get("requirements", [])
            if requirements and all(item["status"] == "PASS" for item in requirements):
                gaps = [
                    gap
                    for gap in gaps
                    if gap
                    not in {
                        "Mapped UI flows have no confirmed requirement CHECKS links.",
                        "Retrieved documents are relevance evidence; requirement semantic approval remains separate.",
                    }
                ]
                gaps.append(
                    "Reviewed requirement contracts passed for this scenario; other retrieved requirements remain unvalidated."
                )
        else:
            gaps.append(
                "Findings describe potential impact; semantic correctness and comparative UI behavior are not independently verified."
            )
            if verification.policy.enabled:
                gaps.append(
                    "Verification: "
                    + behavior.get("reason", behavior.get("stop_reason") or behavior["status"])
                )
        report: AnalysisReportPayload = {
            "schema_version": 1,
            "workflow_version": WORKFLOW_VERSION,
            "run_id": context.run_id,
            "request": state["request"],
            "status": status,
            "completeness": (
                "COMPLETE_FOR_CONFIGURED_SCOPE"
                if status == "COMPLETED"
                and state.get("findings")
                and runtime_attestation["status"] == "VERIFIED"
                and behavior["status"] == "COMPLETED"
                else "PARTIAL"
            ),
            "verification": behavior["status"],
            "verification_plan": state.get("verification_plan", {}),
            "verification_approval": {
                "mode": verification.policy.approval,
                "approved": state.get("verification_approved", False),
                "scenario_id": state.get("verification_plan", {}).get("scenario_id"),
                "policy_fingerprint": digest(verification.fingerprint),
            },
            "human_review": {
                "policy": human_review_policy.policy,
                "status": state.get("review_outcome", "NOT_REQUESTED"),
                "requests": state.get("review_requests", []),
                "follow_up_verification_allowed": (
                    human_review_policy.policy == "non_blocking"
                    and human_review_policy.allow_follow_up_verification
                    and any(
                        item.get("kind") == "verification_approval" and item.get("status") == "NOT_ANSWERED"
                        for item in state.get("review_requests", [])
                    )
                ),
            },
            "behavior_verification": behavior,
            "runtime_attestation": runtime_attestation,
            "stop_reason": stop_reason,
            "findings": state.get("findings", []),
            "evidence": state.get("evidence", {}),
            "gaps": gaps,
            "tool_usage": runtime.ledger.usage(context.run_id),
            "limit_events": [
                e for e in runtime.ledger.events(context.run_id) if e["kind"] == "LIMIT_REACHED"
            ],
            "audit_events": runtime.ledger.events(context.run_id),
            "model_guardrails": {
                "status": (
                    "SANITIZED"
                    if any('"status":"SANITIZED"' in event["detail"] for event in guardrail_events)
                    else "PASSED"
                ),
                "events": guardrail_events,
                "policy_fingerprint": runtime.guardrails.version.split(":", 1)[1],
            },
            "exploration": {
                "status": state.get("exploration_status", "DISABLED"),
                "environment": exploration.environment,
                "goal": exploration.goal if exploration.enabled else None,
                "steps": state.get("exploration_steps", 0),
            },
            "ui_knowledge": ui_index(state.get("evidence", {})),
        }
        return {"report": report}

    def plan_verification(state: AnalysisState) -> AnalysisState:
        plan = verification.select(state, scoped_context(state))
        if plan["status"] != "SELECTED":
            return {"verification_plan": plan, "verification_result": skipped(plan["reason"])}
        gap = verification.budget_gap(runtime, context, plan)
        if gap:
            return {
                "verification_plan": {**plan, "status": "BLOCKED_BUDGET"},
                "verification_result": skipped(gap, status="BLOCKED_BUDGET"),
            }
        return {
            "verification_plan": plan,
            "verification_approved": verification.policy.approval == "preapproved",
        }

    def verification_review(state: AnalysisState) -> AnalysisState:
        if state["review_count"] >= limits.max_review_requests:
            return {
                "verification_approved": False,
                "verification_result": skipped("Human review request limit reached", status="BLOCKED_BUDGET"),
            }
        plan = state["verification_plan"]
        question = f"Approve scenario {plan['scenario_id']}: {plan['description']}? Reply approve or reject."
        request: ReviewRequestPayload = {
            "kind": "verification_approval",
            "question": question,
            "scenario_id": plan["scenario_id"],
            "scenario": plan,
            "status": "NOT_ANSWERED",
            "answer": None,
        }
        if human_review_policy.policy == "non_blocking":
            return {
                "verification_approved": False,
                "verification_result": skipped(
                    "Human approval was not received; approval-dependent verification was not executed",
                    status="NOT_EXECUTED",
                ),
                "review_requests": [
                    *state.get("review_requests", []),
                    request,
                ],
                "review_count": state["review_count"] + 1,
                "review_outcome": "NOT_ANSWERED",
            }
        response = interrupt(
            {
                "question": question,
                "run_id": context.run_id,
                "scenario": plan,
                "instruction": "Only an exact approve response authorizes this scenario. Budgets and configured scope remain unchanged.",
            }
        )
        try:
            answer = ReviewResponse.model_validate(response).answer
        except ValidationError:
            answer = "invalid response"
        approved = answer.strip().casefold() == "approve"
        completed_request: ReviewRequestPayload = {
            **request,
            "status": "APPROVED" if approved else "REJECTED",
            "answer": answer,
        }
        return {
            "verification_approved": approved,
            "reviews": [*state["reviews"], answer],
            "review_requests": [
                *state.get("review_requests", []),
                completed_request,
            ],
            "review_count": state["review_count"] + 1,
            "review_outcome": "APPROVED" if approved else "REJECTED",
            **(
                {}
                if approved
                else {"verification_result": skipped("Scenario was not approved", status="NOT_EXECUTED")}
            ),
        }

    def verify_behavior(state: AnalysisState) -> AnalysisState:
        if not state.get("verification_approved"):
            return {"verification_result": skipped("Scenario was not approved", status="NOT_EXECUTED")}
        try:
            result = verification.execute(runtime, scoped_context(state), state["verification_plan"])
            evidence = merge_evidence(
                state["evidence"], [Evidence.model_validate(e) for e in result.get("evidence", {}).values()]
            )
        except (ValueError, OSError, KeyError) as exc:
            result = skipped(
                f"Verification evidence could not be recovered ({type(exc).__name__}); reconcile before retrying",
                status="BLOCKED_UNCERTAIN",
            )
            evidence = state["evidence"]
        return {
            "verification_result": result,
            "evidence": evidence,
            "usage": runtime.ledger.usage(context.run_id),
        }

    def route_verification(state: AnalysisState) -> str:
        if state["verification_plan"]["status"] != "SELECTED":
            return "finalize"
        return "verify_behavior" if state.get("verification_approved") else "verification_review"

    def continue_to(target: str) -> Callable[[AnalysisState], str]:
        return lambda state: "finalize" if state.get("stop_reason") else target

    def prepare_exploration(state: AnalysisState) -> AnalysisState:
        status = exploration_status(state, exploration, limits, context.agent_id)
        if "browser.navigate" not in runtime.registry.tools and exploration.enabled:
            status = "NO_BROWSER"
        return {"exploration_status": status, "phase": "exploration" if status == "ACTIVE" else "analysis"}

    def finish_exploration(state: AnalysisState) -> AnalysisState:
        return {
            "phase": "analysis",
            "exploration_status": "MODEL_STOPPED",
            "usage": runtime.ledger.usage(context.run_id),
        }

    def after_action(state: AnalysisState) -> str:
        if state.get("stop_reason"):
            return "finalize"
        return "prepare_exploration" if state.get("phase") == "exploration" else "reason"

    def after_review(state: AnalysisState) -> str:
        if human_review_policy.policy == "non_blocking":
            return "finalize"
        return after_action(state)

    def route_decision(state: AnalysisState) -> str:
        if state.get("stop_reason"):
            return "finalize"
        if state.get("phase") == "exploration" and state["decision"]["action"] == "finish":
            return "finish_exploration"
        return {"tool": "execute_tool", "review": "human_review", "finish": "validate_findings"}[
            state["decision"]["action"]
        ]

    builder = StateGraph(AnalysisState)
    for name, function in (
        ("fetch_changes", fetch_changes),
        ("attest_deployments", attest_deployments),
        ("retrieve", retrieve),
        ("observe_entries", observe_entries),
        ("prepare_exploration", prepare_exploration),
        ("finish_exploration", finish_exploration),
        ("reason", reason),
        ("execute_tool", execute_tool),
        ("validate_findings", validate_findings),
        ("human_review", human_review),
        ("plan_verification", plan_verification),
        ("verification_review", verification_review),
        ("verify_behavior", verify_behavior),
        ("finalize", finalize),
    ):
        builder.add_node(name, function)
    builder.add_edge(START, "fetch_changes")
    builder.add_conditional_edges("fetch_changes", continue_to("attest_deployments"))
    builder.add_conditional_edges("attest_deployments", continue_to("retrieve"))
    builder.add_conditional_edges("retrieve", continue_to("observe_entries"))
    builder.add_conditional_edges("observe_entries", continue_to("prepare_exploration"))
    builder.add_edge("prepare_exploration", "reason")
    builder.add_edge("finish_exploration", "reason")
    builder.add_conditional_edges("reason", route_decision)
    builder.add_conditional_edges("execute_tool", after_action)
    builder.add_conditional_edges("human_review", after_review)
    builder.add_conditional_edges(
        "validate_findings",
        lambda state: (
            "plan_verification"
            if state.get("stop_reason") == "ANALYSIS_FINISHED"
            else ("finalize" if state.get("stop_reason") else "reason")
        ),
    )
    builder.add_conditional_edges("plan_verification", route_verification)
    builder.add_conditional_edges(
        "verification_review",
        lambda state: "verify_behavior" if state.get("verification_approved") else "finalize",
    )
    builder.add_edge("verify_behavior", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
