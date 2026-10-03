import json

import pytest

from trace_coordinator import AnalysisRequest, CallLimits, Coordinator
from trace_coordinator.application.ui_evidence import label_in_source, source_lines, ui_index
from trace_coordinator.application.ui_exploration import ui_exploration_status
from trace_coordinator.config import UIExplorationConfig
from trace_coordinator.domain.models import Decision, Evidence, ToolResult
from trace_coordinator.infrastructure.adapters.browser import ActionInput, NavigateInput
from trace_coordinator.infrastructure.adapters.fixtures import FixtureTool


def screen(ref, *, name="Product", fingerprint="home", previous=None):
    return Evidence(
        id=ref,
        project_id="p",
        kind="browser",
        source="https://example.test/",
        summary=json.dumps(
            {
                "environment": "patched",
                "url": "https://example.test/",
                "state_fingerprint": fingerprint,
                "elements": [{"id": "element-1", "name": name}],
            }
        ),
        metadata={
            "transition_record": {
                "from": previous,
                "to": ref,
                "environment": "patched",
                "tool": "browser.act",
                "element_name": name,
            }
        },
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "state,options,expected",
    [
        ({}, {"enabled": False}, "DISABLED"),
        ({}, {}, "NO_OBSERVATION"),
        ({"evidence": {"s": screen("s", name="Promo code")}}, {}, "TARGET_OBSERVED"),
        ({"evidence": {"s": screen("s"), "t": screen("t")}}, {}, "REPEATED_STATE"),
        ({"ui_exploration_steps": 4}, {}, "STEP_LIMIT"),
        ({"rounds": 9}, {}, "REPORT_BUDGET_RESERVED"),
        (
            {"usage": [{"agent": "coordinator", "tool": "model.decide", "attempts": 4}]},
            {},
            "REPORT_BUDGET_RESERVED",
        ),
        (
            {"usage": [{"agent": "coordinator", "tool": "browser.act", "attempts": 29}]},
            {},
            "REPORT_BUDGET_RESERVED",
        ),
        ({"evidence": {"s": screen("s")}}, {}, "ACTIVE"),
    ],
)
def test_ui_exploration_bounds(state, options, expected):
    initial = {"evidence": {"s": screen("s")}}
    if expected == "NO_OBSERVATION":
        initial = {}
    policy = UIExplorationConfig(enabled=True, target_controls=("Promo code",)).model_copy(update=options)
    assert ui_exploration_status({**initial, **state}, policy, CallLimits(), "coordinator") == expected


@pytest.mark.parametrize("reach_target", [False, True])
def test_workflow_discovers_controls_and_reserves_report_attempt(tmp_path, reach_target):
    decisions = []

    class Browser:
        allowed_agents = frozenset({"coordinator"})
        description = "Controlled browser fixture"
        version = "v1"

        def __init__(self, name):
            self.name = name
            self.input_model = NavigateInput if name == "browser.navigate" else ActionInput
            self.count = 0

        def execute(self, arguments, context):
            self.count += 1
            ref = f"{self.name}-{arguments.environment}-{self.count}"
            data = screen(
                ref,
                name="Promo code" if reach_target and self.name == "browser.act" else "Product",
                fingerprint=ref,
                previous=getattr(arguments, "snapshot_id", None),
            )
            data["summary"] = json.dumps(
                {**json.loads(data["summary"]), "environment": arguments.environment}
            )
            return ToolResult(evidence=(Evidence.model_validate(data),))

    class Model:
        version = "v1"

        def decide(self, payload):
            decisions.append(payload["phase"])
            if payload["phase"] == "ui_exploration":
                assert len(payload["evidence"]) == 1
                assert payload["latest_snapshot_id"] in payload["evidence"]
                assert all(t["name"].startswith("browser.") for t in payload["tools"])
                latest = [
                    e
                    for e in payload["evidence"].values()
                    if e["kind"] == "browser" and json.loads(e["summary"])["environment"] == "patched"
                ][-1]
                return Decision(
                    action="tool",
                    tool="browser.act",
                    arguments={
                        "environment": "patched",
                        "snapshot_id": latest["id"],
                        "element_id": "element-1",
                        "action": "click",
                    },
                )
            return Decision(action="finish")

    tools = [
        FixtureTool(
            "github.diff",
            {
                "evidence": [
                    {"id": "diff", "project_id": "p", "kind": "diff", "summary": "patch", "source": "fixture"}
                ]
            },
        ),
        FixtureTool("knowledge.graph", {}),
        FixtureTool("knowledge.documents", {}),
        Browser("browser.navigate"),
        Browser("browser.act"),
    ]
    coordinator = Coordinator(
        tmp_path,
        CallLimits(),
        tools,
        Model(),
        ui_exploration=UIExplorationConfig(enabled=True, target_controls=("Promo code",)),
    )
    result = coordinator.run(AnalysisRequest(project_id="p", repository="owner/repo", pull_request=1), "run")
    assert result["status"] == "COMPLETED"
    assert decisions[-1] == "analysis"
    assert len(decisions) == (2 if reach_target else 5)
    assert result["ui_exploration"]["status"] == ("TARGET_OBSERVED" if reach_target else "STEP_LIMIT")
    assert all(r["attempts"] <= 5 for r in result["tool_usage"])
    assert result["ui_knowledge"]["discovered_paths"]
    assert (
        coordinator.run(AnalysisRequest(project_id="p", repository="owner/repo", pull_request=1), "run")
        == result
    )


PATCH = """diff --git a/ui.tsx b/ui.tsx
--- a/ui.tsx
+++ b/ui.tsx
@@ -10,2 +10,3 @@
-<input placeholder="Old code" />
+<input placeholder="Promo code" />
+<button>Apply</button>
 const stable = true;
"""


def test_ui_candidates_have_correct_revision_and_lines_and_are_never_confirmed():
    diff = {
        "id": "diff",
        "kind": "diff",
        "summary": PATCH,
        "metadata": {
            "changes": {
                "analysis_base": "a" * 40,
                "analysis_head": "b" * 40,
                "files": [{"path": "ui.tsx", "status": "M"}],
            }
        },
    }
    evidence = {"diff": diff, "s": screen("s", name="Promo code")}
    result = ui_index(evidence)
    candidate = result["code_ui_candidates"][0]
    assert candidate["line"] == 10 and candidate["code_path"] == "ui.tsx"
    assert candidate["configured_revision"] == "b" * 40 and candidate["status"] == "CANDIDATE"
    assert not candidate["runtime_attribution_verified"]
    assert result["graph_publication"] == "NOT_PUBLISHED"
    assert list(source_lines(PATCH, "baseline")) == [
        ("ui.tsx", 10, '<input placeholder="Old code" />'),
        ("ui.tsx", 11, "const stable = true;"),
    ]
    assert not label_in_source("Apply", "const handleApply = () => {};")


def test_no_invented_flow_across_restart_or_environments():
    result = ui_index(
        {
            "s": screen("s"),
            "t": screen("t", previous="unknown"),
            "u": screen("u", previous="t"),
            "restart": screen("restart"),
        }
    )
    paths = result["discovered_paths"]
    assert len(paths) == 1 and len(paths[0]["transitions"]) == 1
    assert paths[0]["transitions"][0]["from"] == "t"
    assert not result["code_ui_candidates"]


def test_ui_exploration_report_explains_paths_and_unconfirmed_candidates():
    from trace_coordinator.presentation.response_formatter import TemplateResponseFormatter

    result = TemplateResponseFormatter().format(
        {
            "status": "COMPLETED",
            "tool_usage": [],
            "ui_exploration": {
                "status": "TARGET_OBSERVED",
                "environment": "patched",
                "steps": 2,
            },
            "ui_knowledge": {
                "discovered_paths": [
                    {
                        "environment": "patched",
                        "transitions": [{"tool": "browser.act", "element_name": "Open\ncart"}],
                    }
                ],
                "code_ui_candidates": [{"label": "Discount code", "code_path": "ui.tsx", "line": 12}],
            },
        }
    )
    assert "Open cart" in result
    assert "ui.tsx:12" in result and "Candidate; needs validation" in result
    assert "No confirmed graph edges" in result
