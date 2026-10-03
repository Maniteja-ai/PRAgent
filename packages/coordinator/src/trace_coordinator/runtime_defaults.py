"""Typed access to the packaged runtime defaults JSON."""

import json
from functools import cache
from importlib.resources import files
from typing import Literal, cast

from trace_coordinator.domain.contracts import JsonObject, as_json_object


@cache
def runtime_defaults() -> JsonObject:
    resource = files("trace_coordinator.resources").joinpath("runtime-defaults.json")
    return as_json_object(json.loads(resource.read_text(encoding="utf-8")))


def default_int(section: str, name: str) -> int:
    value = runtime_defaults().get(section)
    selected = value.get(name) if isinstance(value, dict) else None
    if isinstance(selected, bool) or not isinstance(selected, int):
        raise RuntimeError(f"Runtime default {section}.{name} must be an integer")
    return selected


def default_float(section: str, name: str) -> float:
    value = runtime_defaults().get(section)
    selected = value.get(name) if isinstance(value, dict) else None
    if isinstance(selected, bool) or not isinstance(selected, (int, float)):
        raise RuntimeError(f"Runtime default {section}.{name} must be a number")
    return float(selected)


def default_bool(section: str, name: str) -> bool:
    value = runtime_defaults().get(section)
    selected = value.get(name) if isinstance(value, dict) else None
    if not isinstance(selected, bool):
        raise RuntimeError(f"Runtime default {section}.{name} must be a boolean")
    return selected


def default_string(section: str, name: str) -> str:
    value = runtime_defaults().get(section)
    selected = value.get(name) if isinstance(value, dict) else None
    if not isinstance(selected, str):
        raise RuntimeError(f"Runtime default {section}.{name} must be a string")
    return selected


def default_guardrail_action(
    name: str,
) -> Literal["allow", "block", "redact", "quarantine"]:
    selected = default_string("guardrails", name)
    allowed = {"allow", "block", "redact", "quarantine"}
    if selected not in allowed:
        raise RuntimeError(f"Runtime default guardrails.{name} has an unsupported action")
    return cast(Literal["allow", "block", "redact", "quarantine"], selected)


def default_limit_overrides() -> dict[str, dict[str, int]]:
    calls = runtime_defaults().get("calls")
    selected = calls.get("overrides") if isinstance(calls, dict) else None
    if not isinstance(selected, dict):
        raise RuntimeError("Runtime default calls.overrides must be an object")
    parsed: dict[str, dict[str, int]] = {}
    for agent, tools in selected.items():
        if not isinstance(agent, str) or not isinstance(tools, dict):
            raise RuntimeError("Runtime default calls.overrides must map agents to tools")
        parsed[agent] = {}
        for tool, limit in tools.items():
            if not isinstance(tool, str) or isinstance(limit, bool) or not isinstance(limit, int):
                raise RuntimeError("Runtime default calls.overrides contains an invalid limit")
            parsed[agent][tool] = limit
    return parsed
