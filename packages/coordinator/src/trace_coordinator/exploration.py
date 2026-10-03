"""Deterministic bounds around observation-driven model choices; no hidden tool calls."""

import json


def screens(evidence, environment):
    found = []
    for item in evidence.values():
        if item["kind"] != "browser":
            continue
        try:
            data = json.loads(item["summary"])
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("environment") == environment:
            found.append((item["id"], data))
    return found


def exploration_status(state, policy, limits, agent):
    if not policy.enabled:
        return "DISABLED"
    observed = screens(state.get("evidence", {}), policy.environment)
    if not observed:
        return "NO_OBSERVATION"
    _, latest = observed[-1]
    names = {e.get("name", "").strip().casefold() for e in latest.get("elements", [])}
    if any(name.strip().casefold() in names for name in policy.target_controls):
        return "TARGET_OBSERVED"
    fingerprint = latest.get("state_fingerprint")
    if (
        fingerprint
        and sum(s.get("state_fingerprint") == fingerprint for _, s in observed) >= policy.max_state_visits
    ):
        return "REPEATED_STATE"
    if state.get("exploration_steps", 0) >= policy.max_steps:
        return "STEP_LIMIT"
    usage = state.get("usage", [])
    used_model = sum(r["attempts"] for r in usage if r["agent"] == agent and r["tool"] == "model.decide")
    used_total = sum(r["attempts"] for r in usage)
    # Need one decision + one action + one final report attempt. Retries still
    # consume the same durable budget and may make that reservation insufficient.
    if (
        used_model >= limits.limit_for(agent, "model.decide") - 1
        or state.get("rounds", 0) >= limits.max_rounds - 1
        or used_total > limits.total_calls - 3
    ):
        return "REPORT_BUDGET_RESERVED"
    return "ACTIVE"
