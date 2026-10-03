import argparse
import json
from pathlib import Path

from trace_impact.evals.config import EvaluationConfig
from trace_impact.evals.scoring.retrieval import evaluate


def main():
    parser = argparse.ArgumentParser(
        prog="trace-eval", description="Score exported retrieval results without changing pipeline code."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("config", type=Path)
    schema = commands.add_parser("schema")
    schema.add_argument("--output", type=Path, default=Path("evaluation.schema.json"))
    args = parser.parse_args()
    try:
        if args.command == "schema":
            value = EvaluationConfig.model_json_schema()
            value["$schema"] = "https://json-schema.org/draft/2020-12/schema"
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            print(f"Evaluation schema written to {args.output}")
            return
        path, summary = evaluate(args.config)
        print(json.dumps({"report": str(path), **summary}, indent=2))
        if summary["quality_gate"] in {"FAIL", "INCOMPLETE"}:
            raise SystemExit(1)
    except (ValueError, OSError):
        parser.exit(
            2,
            "Invalid evaluation input: check field mappings, file paths, IDs, and approval/completeness settings.\n",
        )


if __name__ == "__main__":
    main()
