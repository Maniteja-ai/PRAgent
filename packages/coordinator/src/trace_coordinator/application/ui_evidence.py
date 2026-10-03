"""Build observed paths and reviewable code/UI candidates without publishing graph edges."""

import re
from collections.abc import Iterator, Mapping
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from trace_coordinator.application.ui_exploration import screens
from trace_coordinator.domain.contracts import (
    EvidencePayload,
    ObservedPathPayload,
    UICandidateChangePayload,
    UICandidatePayload,
    UIKnowledgePayload,
    UISummaryPayload,
    UITransitionPayload,
)

_CHANGE_SET_ADAPTER: TypeAdapter[UICandidateChangePayload] = TypeAdapter(UICandidateChangePayload)
_TRANSITION_ADAPTER: TypeAdapter[UITransitionPayload] = TypeAdapter(UITransitionPayload)


def label_in_source(name: str, text: str) -> bool:
    value = re.escape(name)
    return bool(re.search(rf"([\"\x27`]){value}\1|>\s*{value}\s*<|^\s*{value}\s*$", text, re.IGNORECASE))


def source_lines(patch: str, environment: Literal["baseline", "patched"]) -> Iterator[tuple[str, int, str]]:
    """Yield actual baseline/head line numbers from unified diff hunks, never inferred files."""
    path, before, after = None, None, None
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            path, before, after = None, None, None
        elif line.startswith("--- a/") and environment == "baseline":
            path = line[6:]
        elif line.startswith("+++ b/") and environment == "patched":
            path = line[6:]
        elif line.startswith("@@ "):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                before, after = map(int, match.groups())
        elif path and before is not None and after is not None and line[:1] in {" ", "+", "-"}:
            marker, text = line[0], line[1:]
            if environment == "baseline" and marker != "+":
                yield path, before, text
            if environment == "patched" and marker != "-":
                yield path, after, text
            before += marker != "+"
            after += marker != "-"


def ui_index(evidence: Mapping[str, EvidencePayload], max_candidates: int = 50) -> UIKnowledgePayload:
    states: list[UISummaryPayload] = []
    flows: list[ObservedPathPayload] = []
    candidates: list[UICandidatePayload] = []
    diffs: list[tuple[EvidencePayload, UICandidateChangePayload]] = []
    for item in evidence.values():
        if item["kind"] != "diff" or "changes" not in item["metadata"]:
            continue
        try:
            changes = _CHANGE_SET_ADAPTER.validate_python(item["metadata"]["changes"])
        except ValidationError:
            continue
        diffs.append((item, changes))
    for environment in ("baseline", "patched"):
        observed = screens(evidence, environment)
        ids = {ref for ref, _ in observed}
        transitions = []
        for ref, screen in observed:
            states.append(
                {
                    "evidence_id": ref,
                    "environment": environment,
                    "url": screen.get("url"),
                    "state_fingerprint": screen.get("state_fingerprint"),
                    "element_count": len(screen.get("elements", [])),
                }
            )
            transition_value = evidence[ref]["metadata"].get("transition_record")
            if transition_value is not None:
                try:
                    transition = _TRANSITION_ADAPTER.validate_python(transition_value)
                except ValidationError:
                    transition = None
                if transition and transition.get("to") == ref and transition.get("from") in ids:
                    transitions.append(transition)
            for diff, changes in diffs:
                revision = changes["analysis_base" if environment == "baseline" else "analysis_head"]
                allowed_files = {f["path"] for f in changes["files"]}
                for element in screen.get("elements", []):
                    name = element.get("name", "").strip()
                    if len(name) < 4:
                        continue
                    for path, number, text in source_lines(diff["summary"], environment):
                        if path not in allowed_files or not label_in_source(name, text):
                            continue
                        candidates.append(
                            {
                                "status": "CANDIDATE",
                                "method": "literal_label_in_diff",
                                "browser_evidence_id": ref,
                                "element_id": element["id"],
                                "label": name,
                                "environment": environment,
                                "diff_evidence_id": diff["id"],
                                "code_path": path,
                                "line": number,
                                "configured_revision": revision,
                                "source_excerpt": text.strip()[:500],
                                "runtime_attribution_verified": False,
                            }
                        )
        if transitions:
            # A restart can create disconnected paths. Group by predecessor links,
            # rather than inventing a transition between independent sessions.
            paths: list[list[UITransitionPayload]] = []
            for transition in transitions:
                prior = next((p for p in paths if p[-1]["to"] == transition["from"]), None)
                if prior is None:
                    paths.append([transition])
                else:
                    prior.append(transition)
            flows.extend(
                {"environment": environment, "status": "OBSERVED_PATH", "transitions": path} for path in paths
            )
    return {
        "schema_version": 1,
        "screens": states,
        "discovered_paths": flows,
        "code_ui_candidates": candidates[:max_candidates],
        "candidates_truncated": len(candidates) > max_candidates,
        "graph_publication": "NOT_PUBLISHED",
        "validation": "Literal label matches are hypotheses for review, not confirmed rendering or behavior.",
    }
