"""Deterministic Markdown rendering for PR impact reports."""


def markdown(report):
    lines = ["# PR impact report", "", f"Status: {report['status']}", ""]
    observability = report.get("observability")
    if observability:
        lines.extend(
            [
                "## Observability",
                "",
                f"Provider: {observability['provider']}; status: {observability['status']}.",
                f"Project: {observability['project']}",
                f"Trace ID: {observability.get('trace_id') or 'not available'}",
                f"Dashboard: {observability['dashboard_url']}",
                "Content capture: disabled",
                "",
            ]
        )
    review = report.get("human_review", {})
    if review.get("requests"):
        lines.extend(["## Human review", "", f"Outcome: {review['status']}", ""])
        for item in review["requests"]:
            lines.extend(
                [
                    f"- Question: {item['question']}",
                    f"- Status: {item['status']}",
                    *([f"- Answer: {item['answer']}"] if item.get("answer") else []),
                    "",
                ]
            )
        if report.get("follow_up"):
            lines.extend(
                [
                    f"Linked parent run: {report['follow_up']['parent_run_id']}",
                    "",
                ]
            )
    behavior = report.get("behavior_verification")
    if behavior and behavior["status"] != "NOT_RUN":
        lines.extend(
            [
                "## Observed behavioral checks",
                "",
                f"Verification execution: {behavior['status']} (separate from analysis findings).",
                "",
            ]
        )
        if behavior.get("checks"):
            lines.extend(["| Environment | Check | Result |", "| --- | --- | --- |"])
            lines.extend(f"| {c['environment']} | {c['name']} | {c['status']} |" for c in behavior["checks"])
        if behavior.get("comparison"):
            lines.extend(["", behavior["comparison"]["reason"]])
        if behavior.get("not_run"):
            lines.extend(["", "Not run: " + ", ".join(behavior["not_run"])])
        if behavior.get("reason") or behavior.get("stop_reason"):
            lines.extend(["", behavior.get("reason") or behavior["stop_reason"]])
        lines.append("")
    elif behavior and report.get("verification_plan", {}).get("reason"):
        lines.extend(["Verification not run: " + report["verification_plan"]["reason"], ""])
    exploration = report.get("exploration", {})
    if exploration.get("status") not in {None, "DISABLED"}:
        lines.extend(
            [
                "## Browser exploration",
                "",
                f"Outcome: {exploration['status']}; environment: {exploration['environment']}; action proposals: {exploration['steps']}.",
                "",
            ]
        )
        for flow in report.get("ui_knowledge", {}).get("discovered_paths", []):
            actions = [t.get("element_name") or t.get("path") or t["tool"] for t in flow["transitions"]]
            lines.extend(
                [
                    f"Observed path ({flow['environment']}): "
                    + " → ".join(" ".join(a.split()) for a in actions),
                    "",
                ]
            )
        candidates = report.get("ui_knowledge", {}).get("code_ui_candidates", [])
        lines.extend(
            [
                f"Code/UI label candidates for review: {len(candidates)}. No confirmed graph edges were published.",
                "",
            ]
        )
        if candidates:
            lines.extend(["| Observed control | Changed source | Status |", "| --- | --- | --- |"])
            for candidate in candidates:
                label = " ".join(candidate["label"].split()).replace("|", "\\|")
                path = candidate["code_path"].replace("|", "\\|")
                lines.append(f"| {label} | {path}:{candidate['line']} | Candidate; needs validation |")
            lines.append("")
    for finding in report.get("findings", []):
        lines.extend(
            [
                f"## {finding['title']}",
                "",
                finding["explanation"],
                "",
                "Classification: potential impact; behavior is not verified.",
                "",
                "Evidence: " + ", ".join(finding["evidence_ids"]),
                "",
            ]
        )
        lines.extend(f"- Proposed check (not run): {check}" for check in finding["checks"])
        lines.append("")
    lines.extend(["## Limits and gaps", ""])
    lines.extend(f"- {gap}" for gap in report.get("gaps", []))
    lines.extend(["", "## Call usage", "", "| Agent | Tool | Attempts |", "| --- | --- | --- |"])
    lines.extend(f"| {r['agent']} | {r['tool']} | {r['attempts']} |" for r in report["tool_usage"])
    return "\n".join(lines) + "\n"
