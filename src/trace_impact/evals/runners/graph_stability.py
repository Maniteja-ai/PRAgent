"""Repeat one bounded input against real Neo4j; score only after retrieval finishes.

python -m trace_impact.evals.runners.graph_stability configs/evals/graph-stability.json
This measures a fixed synthetic graph query, not 100 complete agent or model runs.
"""

import argparse
import hashlib
import json
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from trace_impact import Settings, create_pipeline
from trace_impact.evals import evaluate
from trace_impact.evals.runners.graph_benchmark import check_witnesses, save
from trace_impact.shared.graph_models import GraphSnapshot


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: Path
    environment_file: Path
    case_id: str = Field(min_length=1)
    repetitions: int = Field(default=100, ge=1, le=100)
    output_directory: Path


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    config = Config.model_validate_json(args.config.read_text(encoding="utf-8"))
    base = args.config.resolve().parent
    dataset = (base / config.dataset).resolve()
    load_dotenv(base / config.environment_file, override=False)
    run = (
        base
        / config.output_directory
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    )
    run.mkdir(parents=True)
    print(f"RUN_DIRECTORY={run.resolve()}", flush=True)
    fixture_path = dataset / "inputs/graph/fixture.json"
    queries_path = dataset / "inputs/graph/queries.jsonl"
    query = next(q for q in rows(queries_path) if q["id"] == config.case_id)
    snapshot = GraphSnapshot.model_validate_json(fixture_path.read_text(encoding="utf-8"))
    snapshot = snapshot.model_copy(update={"id": "stability-" + uuid.uuid4().hex})
    save(
        run / "contract.json",
        {
            "config": config.model_dump(mode="json"),
            "graph_id": snapshot.id,
            "mode": "fresh_live_queries_same_immutable_synthetic_graph_no_model_calls",
            "sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (fixture_path, queries_path, args.config, Path(__file__))
            },
        },
    )
    results = []
    with create_pipeline(Settings.from_env()) as app:
        save(run / "publication.json", app.publish_code_graph(snapshot))
        for i in range(config.repetitions):
            started = perf_counter()
            attempt = {"id": f"{config.case_id}-{i + 1:03d}"}
            try:
                result = app.retrieve_impact(snapshot.id, query)
                check_witnesses(snapshot, query, result)
                attempt.update(execution_status="OK", **result.model_dump(mode="json"))
            except Exception as exc:
                attempt.update(execution_status="ERROR", status="ERROR", error_type=type(exc).__name__)
            attempt["seconds"] = perf_counter() - started
            results.append(attempt)
            # Completed attempts remain available even if a later attempt is interrupted.
            save(run / f"attempt-{i + 1:03d}.json", attempt)
            if (i + 1) % 10 == 0:
                print(f"Completed {i + 1}/{config.repetitions} attempts", flush=True)
    (run / "predictions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    # Retrieval has no access to reference labels. Repetition duplicates the same
    # reference, not the number of independent coverage examples.
    reference = next(r for r in rows(dataset / "labels/graph-cases.jsonl") if r["id"] == config.case_id)
    (run / "references.jsonl").write_text(
        "".join(json.dumps({**reference, "id": r["id"]}) + "\n" for r in results), encoding="utf-8"
    )
    scores = {}
    for field in ("ui_ids", "flow_ids", "requirement_ids"):
        path = run / f"evaluation-{field}.json"
        save(
            path,
            {
                "dataset": "references.jsonl",
                "predictions": "predictions.jsonl",
                "output_directory": "scores",
                "task": "set_retrieval",
                "reference": {
                    "expected_ids": f"reference.{field}",
                    "expected_status": "reference.status",
                    "complete": True,
                },
                "prediction": {"items": field},
                "gates": {"precision_min": 1.0, "recall_min": 1.0},
            },
        )
        report, summary = evaluate(path)
        scores[field] = {"report": str(report.resolve()), "summary": summary}
    completed = [r for r in results if r["execution_status"] == "OK"]
    correct = sum(
        r["execution_status"] == "OK"
        and r["status"] == reference["reference"]["status"]
        and all(set(r.get(f, [])) == set(reference["reference"][f]) for f in scores)
        for r in results
    )
    fingerprints = {
        hashlib.sha256(
            json.dumps({k: v for k, v in r.items() if k not in {"id", "seconds"}}, sort_keys=True).encode()
        ).hexdigest()
        for r in completed
    }
    summary = {
        "scheduled": config.repetitions,
        "completed": len(completed),
        "correct": correct,
        "completion_rate": len(completed) / config.repetitions,
        "correct_rate": correct / config.repetitions,
        "distinct_success_outputs": len(fingerprints),
        "mean_seconds": statistics.mean(r["seconds"] for r in results),
        "max_seconds": max(r["seconds"] for r in results),
        "scores": scores,
        "limits": [
            "One fixed synthetic case, repeated live; not 100 independent scenarios.",
            "No LLM generation, UI exploration, or end-to-end agent stability measured.",
            "Labels are development fixtures, not independently reviewed product gold.",
        ],
    }
    save(run / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "scores"}, indent=2), flush=True)
    return int(correct != config.repetitions)


if __name__ == "__main__":
    raise SystemExit(main())
