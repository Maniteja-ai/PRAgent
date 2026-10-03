"""Run and resume the same immutable request; no credentials are command-line values."""

import argparse
import json
from pathlib import Path

from trace_coordinator.bootstrap import create_coordinator
from trace_coordinator.config import schema
from trace_coordinator.models import AnalysisRequest, ReviewResponse


def markdown(report):
    lines = ["# PR impact report", "", f"Status: {report['status']}", ""]
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("schema")
    generate.add_argument("output", type=Path)
    mappings = commands.add_parser(
        "map-ui", help="Validate saved UI/code evidence; optionally publish an immutable graph"
    )
    mappings.add_argument("config", type=Path)
    mappings.add_argument("--publish", action="store_true", help="Write and verify the new Neo4j snapshot")
    verify = commands.add_parser(
        "verify-voucher", help="Run bounded sandbox UI checks with API fixture verification"
    )
    verify.add_argument("config", type=Path)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--output", type=Path, required=True)
    evaluate = commands.add_parser("evaluate", help="Run the reviewed offline coordinator golden dataset")
    evaluate.add_argument("dataset", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    stability = commands.add_parser(
        "evaluate-stability", help="Repeat the full offline coordinator contract and measure agreement"
    )
    stability.add_argument("dataset", type=Path)
    stability.add_argument("--repetitions", type=int, default=100)
    stability.add_argument("--output", type=Path, required=True)
    campaign = commands.add_parser(
        "evaluation-campaign", help="Gate all saved ingestion, retrieval, graph, LLM and workflow evidence"
    )
    campaign.add_argument("config", type=Path)
    campaign.add_argument("--output", type=Path, required=True)
    real_pr = commands.add_parser(
        "evaluate-real-prs", help="Score UI/flow/requirement relevance and claim faithfulness"
    )
    real_pr.add_argument("dataset", type=Path)
    real_pr.add_argument("predictions", type=Path)
    real_pr.add_argument("--output", type=Path, required=True)
    webhook_serve = commands.add_parser("webhook-serve", help="Serve authenticated GitHub webhooks")
    webhook_serve.add_argument("config", type=Path)
    webhook_serve.add_argument("--host", default="127.0.0.1")
    webhook_serve.add_argument("--port", type=int, default=8000)
    webhook_once = commands.add_parser("webhook-run-once", help="Run one queued GitHub PR job")
    webhook_once.add_argument("config", type=Path)
    additional = commands.add_parser("verify-additional", help="Run one bounded Saleor API behavior oracle")
    additional.add_argument("config", type=Path)
    additional.add_argument("--run-id", required=True)
    additional.add_argument("--output", type=Path, required=True)
    live_llm = commands.add_parser(
        "llm-evaluate", help="Call the configured LLM with reviewed grounding and safety cases"
    )
    live_llm.add_argument("dataset", type=Path)
    live_llm.add_argument("--output", type=Path, required=True)
    readiness = commands.add_parser("release-check", help="Evaluate all declared production release gates")
    readiness.add_argument("config", type=Path)
    readiness.add_argument("--output", type=Path, required=True)
    production_schema = commands.add_parser("production-schema")
    production_schema.add_argument("output_directory", type=Path)
    run = commands.add_parser("run")
    run.add_argument("config", type=Path)
    run.add_argument("request", type=Path)
    run.add_argument("--run-id", required=True)
    run.add_argument("--review", type=Path, help="JSON with an answer to a pending review")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "production-schema":
        from trace_coordinator.additional_behavior import additional_behavior_schema
        from trace_coordinator.campaign import campaign_schema
        from trace_coordinator.evaluation import dataset_schema
        from trace_coordinator.github_webhook import webhook_schema
        from trace_coordinator.llm_evaluation import live_llm_schema
        from trace_coordinator.readiness import gate_schema
        from trace_coordinator.real_pr_evaluation import real_pr_dataset_schema

        args.output_directory.mkdir(parents=True, exist_ok=True)
        (args.output_directory / "golden-dataset.schema.json").write_text(
            json.dumps(dataset_schema(), indent=2) + "\n", encoding="utf-8"
        )
        (args.output_directory / "production-gate.schema.json").write_text(
            json.dumps(gate_schema(), indent=2) + "\n", encoding="utf-8"
        )
        (args.output_directory / "live-llm-evaluation.schema.json").write_text(
            json.dumps(live_llm_schema(), indent=2) + "\n", encoding="utf-8"
        )
        (args.output_directory / "evaluation-campaign.schema.json").write_text(
            json.dumps(campaign_schema(), indent=2) + "\n", encoding="utf-8"
        )
        (args.output_directory / "real-pr-evaluation.schema.json").write_text(
            json.dumps(real_pr_dataset_schema(), indent=2) + "\n", encoding="utf-8"
        )
        (args.output_directory / "github-webhook.schema.json").write_text(
            json.dumps(webhook_schema(), indent=2) + "\n", encoding="utf-8"
        )
        (args.output_directory / "additional-behavior.schema.json").write_text(
            json.dumps(additional_behavior_schema(), indent=2) + "\n", encoding="utf-8"
        )
        return
    if args.command == "verify-additional":
        from trace_coordinator.additional_behavior import run_additional_behavior

        result = run_additional_behavior(args.config, args.run_id)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "checks": result["checks"]}))
        return
    if args.command == "webhook-serve":
        try:
            import uvicorn
        except ImportError as exc:
            raise ValueError("Install the coordinator 'webhook' extra to serve HTTP") from exc
        from trace_coordinator.github_webhook import create_webhook_app

        uvicorn.run(create_webhook_app(args.config), host=args.host, port=args.port)
        return
    if args.command == "webhook-run-once":
        from trace_coordinator.github_webhook import run_next_job

        print(json.dumps(run_next_job(args.config)))
        return
    if args.command == "evaluate-real-prs":
        from trace_coordinator.real_pr_evaluation import evaluate_real_prs, real_pr_markdown

        result = evaluate_real_prs(args.dataset, args.predictions)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(real_pr_markdown(result), encoding="utf-8")
        print(json.dumps({"status": result["status"], "metrics": result["metrics"]}))
        return
    if args.command == "evaluation-campaign":
        from trace_coordinator.campaign import campaign_markdown, run_campaign

        result = run_campaign(args.config)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(campaign_markdown(result), encoding="utf-8")
        print(json.dumps({"status": result["status"], "summary": result["summary"]}))
        return
    if args.command == "evaluate-stability":
        from trace_coordinator.evaluation import evaluate_stability, stability_markdown

        result = evaluate_stability(args.dataset, args.output, args.repetitions)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(stability_markdown(result), encoding="utf-8")
        print(json.dumps({"status": result["status"], "metrics": result["metrics"]}))
        return
    if args.command == "llm-evaluate":
        from trace_coordinator.llm_evaluation import evaluate_live_llm, live_llm_markdown

        result = evaluate_live_llm(args.dataset, args.output)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(live_llm_markdown(result), encoding="utf-8")
        print(json.dumps({"status": result["status"], "metrics": result["metrics"]}))
        return
    if args.command == "evaluate":
        from trace_coordinator.evaluation import evaluate_dataset, evaluation_markdown

        result = evaluate_dataset(args.dataset, args.output)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(evaluation_markdown(result), encoding="utf-8")
        print(json.dumps({"status": result["status"], "metrics": result["metrics"]}))
        return
    if args.command == "release-check":
        from trace_coordinator.readiness import readiness_markdown, run_gate

        result = run_gate(args.config)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(readiness_markdown(result), encoding="utf-8")
        print(json.dumps({"status": result["status"], "output": str(args.output)}))
        return
    if args.command == "verify-voucher":
        from trace_coordinator.verification import run_verification, verification_markdown

        result = run_verification(args.config, args.run_id)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(verification_markdown(result), encoding="utf-8")
        print(json.dumps({"status": result["status"], "run_id": args.run_id, "checks": result["checks"]}))
        return
    if args.command == "map-ui":
        from trace_coordinator.adapters.knowledge import prepare_ui_snapshot, publish_ui_snapshot

        if args.publish:
            receipt, saved = publish_ui_snapshot(args.config)
            print(
                json.dumps({"status": receipt["status"], "graph_id": receipt["graph_id"], "receipt": saved})
            )
        else:
            _, _, _, prepared = prepare_ui_snapshot(args.config)
            print(json.dumps(prepared, indent=2))
        return
    if args.command == "schema":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(schema(), indent=2) + "\n", encoding="utf-8")
        return
    request = AnalysisRequest.model_validate_json(args.request.read_text(encoding="utf-8-sig"))
    review = (
        ReviewResponse.model_validate_json(args.review.read_text(encoding="utf-8-sig"))
        if args.review
        else None
    )
    with create_coordinator(args.config) as coordinator:
        result = coordinator.run(request, args.run_id, review=review)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "run_id": args.run_id, "output": str(args.output)}))


if __name__ == "__main__":
    main()
