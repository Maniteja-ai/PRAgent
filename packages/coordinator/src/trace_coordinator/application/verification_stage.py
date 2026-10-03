"""Coordinator verification policy and crash boundary, independent of providers."""

import json
from fnmatch import fnmatchcase
from typing import Protocol

from trace_coordinator.application.mapping import verified_bytes
from trace_coordinator.domain.contracts import JsonObject
from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import Evidence, ToolContext
from trace_coordinator.infrastructure.artifacts import save_artifact
from trace_coordinator.infrastructure.ledger import canonical, digest


class ApprovedScenario(Protocol):
    id: str
    version: str
    description: str
    changed_paths: tuple[str, ...]
    required_calls: dict[str, int]

    def execute(self, runtime: object, context: ToolContext) -> JsonObject: ...


def skipped(reason, *, status="NOT_RUN"):
    return {"status": status, "checks": [], "evidence": {}, "reason": reason}


class VerificationStage:
    def __init__(self, policy, scenarios, artifact_root):
        self.policy, self.root = policy, artifact_root
        self.scenarios = {scenario.id: scenario for scenario in scenarios}
        if len(self.scenarios) != len(scenarios):
            raise ValueError("Duplicate scenario implementation")
        if policy.enabled and set(self.scenarios) != {item.id for item in policy.scenarios}:
            raise ValueError("Configured and implemented scenario catalogs differ")
        for scenario in scenarios:
            if not scenario.required_calls or any(
                type(n) is not int or not 1 <= n <= 5 for n in scenario.required_calls.values()
            ):
                raise ValueError("Scenario budget must contain bounded positive canonical tool counts")

    @property
    def fingerprint(self):
        return {
            "policy": self.policy.model_dump(mode="json"),
            "catalog": {
                key: {"version": value.version, "paths": value.changed_paths, "calls": value.required_calls}
                for key, value in sorted(self.scenarios.items())
            },
        }

    def select(self, state, context):
        if not self.policy.enabled:
            return {"status": "NOT_RUN", "reason": "Verification is disabled"}
        if not state.get("findings") or context.changes is None:
            return {"status": "NOT_RUN", "reason": "No validated impact findings or pinned change set"}
        matched = [
            s
            for s in self.scenarios.values()
            if any(
                fnmatchcase(file.path, pattern)
                for file in context.changes.files
                for pattern in s.changed_paths
            )
        ]
        if len(matched) != 1:
            return {
                "status": "NOT_RUN",
                "reason": "No approved scenario matches"
                if not matched
                else "Multiple approved scenarios match; select a narrower catalog before running",
            }
        scenario = matched[0]
        return {
            "status": "SELECTED",
            "scenario_id": scenario.id,
            "description": scenario.description,
            "matched_files": [
                f.path
                for f in context.changes.files
                if any(fnmatchcase(f.path, p) for p in scenario.changed_paths)
            ],
            "finding_titles": [f["title"] for f in state["findings"]],
            "required_calls": scenario.required_calls,
            "approval": self.policy.approval,
        }

    def budget_gap(self, runtime, context, plan):
        usage = runtime.ledger.usage(context.run_id)
        counts = {row["tool"]: row["attempts"] for row in usage if row["agent"] == context.agent_id}
        for tool, count in plan["required_calls"].items():
            if counts.get(tool, 0) + count > runtime.limits.limit_for(context.agent_id, tool):
                return f"Insufficient remaining {tool} attempts; verification has no separate allowance"
        if (
            sum(row["attempts"] for row in usage) + sum(plan["required_calls"].values())
            > runtime.limits.total_calls
        ):
            return "Insufficient remaining total call budget"
        if runtime.ledger.active_elapsed(context.run_id) >= runtime.limits.max_run_seconds:
            return "Run deadline exceeded before verification"
        return None

    def execute(self, runtime, context, plan):
        scenario = self.scenarios[plan["scenario_id"]]
        identity = digest({"scenario": scenario.id, "version": scenario.version, "plan": plan})
        started = False
        for event in runtime.ledger.events(context.run_id):
            if event["kind"] not in {"VERIFICATION_STARTED", "VERIFICATION_SAVED"}:
                continue
            record = json.loads(event["detail"])
            if record["identity"] != identity:
                return skipped("A different verification was already started in this run", status="BLOCKED")
            if event["kind"] == "VERIFICATION_SAVED":
                return json.loads(verified_bytes(record["artifact"]))
            started = True
        if started:
            return skipped(
                "Verification was interrupted. Reconcile the existing attempts; carts and browser actions will not be recreated automatically.",
                status="BLOCKED_UNCERTAIN",
            )
        gap = self.budget_gap(runtime, context, plan)
        if gap:
            return skipped(gap, status="BLOCKED_BUDGET")
        runtime.ledger.event(context.run_id, "VERIFICATION_STARTED", canonical({"identity": identity}))
        try:
            result = scenario.execute(runtime, context)
            # External provider boundaries must not inject foreign evidence or dangling references.
            if result.get("status") not in {"COMPLETED", "BLOCKED"}:
                raise ToolFailure("Scenario returned an invalid execution status")
            if not result.get("checks"):
                raise ToolFailure("Scenario returned no check outcomes")
            evidence = {key: Evidence.model_validate(value) for key, value in result["evidence"].items()}
            if any(key != item.id or item.project_id != context.project_id for key, item in evidence.items()):
                raise ToolFailure("Scenario evidence has an invalid project or identity")
            if any(
                c["status"] not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}
                or c.get("environment") not in {"baseline", "patched"}
                or not c.get("name")
                or (c["status"] in {"PASS", "FAIL"} and not c["evidence_ids"])
                or any(ref not in evidence for ref in c["evidence_ids"])
                for c in result["checks"]
            ):
                raise ToolFailure("Scenario returned invalid check evidence")
        except Exception as exc:
            # Side effects might have succeeded. Never retry or expose raw provider errors.
            result = skipped(
                f"Verification stopped ({type(exc).__name__}); inspect the attempt ledger before retrying",
                status="BLOCKED_UNCERTAIN",
            )
        saved = save_artifact(
            self.root, context.run_id, canonical(result).encode(), ".integrated-verification.json"
        )
        runtime.ledger.event(
            context.run_id, "VERIFICATION_SAVED", canonical({"identity": identity, "artifact": saved})
        )
        return result
