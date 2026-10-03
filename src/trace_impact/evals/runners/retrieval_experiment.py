"""Evaluate new stages against saved candidates without repeating embedding calls.

Usage: python -m trace_impact.evals.runners.retrieval_experiment configs/evals/gemini-reranking.json
Resume with --run-dir <printed directory>. Failed queries remain errors until retried.
"""

import argparse
import hashlib
import json
import platform
import uuid
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv
from filelock import FileLock

from trace_impact import Settings, create_pipeline
from trace_impact.evals import evaluate
from trace_impact.retrieval import (
    Passage,
    RetrievalResult,
    RetrievalScope,
    build_retrieval_service,
    load_retrieval_config,
)


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def jsonl(path, values):
    temp = path.with_suffix(".tmp")
    temp.write_text("".join(json.dumps(value, allow_nan=False) + "\n" for value in values), encoding="utf-8")
    temp.replace(path)


class ReplayRetriever:
    """Input-only adapter for one saved, scoped candidate list; no labels are loaded."""

    def __init__(self, passages, candidates, scores):
        self.passages, self.candidates, self.scores = passages, candidates, scores

    def retrieve(self, query, scope, limit):
        result = []
        for id, score in zip(self.candidates[:limit], self.scores[:limit], strict=True):
            p = self.passages[id]
            result.append(
                Passage(
                    id=id,
                    scope=RetrievalScope(project_id=p["project_id"], run_id=p["snapshot_id"]),
                    source_id=p["source_id"],
                    text=p["text"],
                    artifact_path=p["evidence"]["source_path"],
                    retrieval_score=score,
                    metadata={"source_url": p["source_url"], "source_version": p["source_version"]},
                )
            )
        return tuple(result)


def score_outputs(run, labels):
    """Separate scoring stage. Pipeline execution never receives the labels."""
    base = {
        "dataset": str(labels),
        "output_directory": str(run / "scores"),
        "reference": {
            "expected_ids": None,
            "relevance_grades": "reference.relevance_grades",
            "candidate_universe": "reference.candidate_universe",
            "evidence_units": "reference.evidence_units",
            "complete": True,
        },
    }
    scores = {}
    for name, task, k in [(f"reranked-k{k}", "ranked_retrieval", k) for k in (1, 2, 3, 5, 10)] + [
        ("selected", "set_retrieval", 5)
    ]:
        config = {
            **base,
            "task": task,
            "k": k,
            "predictions": str(run / ("selected.jsonl" if name == "selected" else "ranked.jsonl")),
        }
        path = run / f"{name}-evaluation.json"
        save(path, config)
        report, summary = evaluate(path)
        scores[name] = {"report": str(report), "summary": summary}
    gold = rows(labels)
    predictions = {p["id"]: p for p in rows(run / "selected.jsonl")}
    evidence_coverage, false_abstentions, negatives = [], 0, []
    for case in gold:
        prediction = predictions[case["id"]]
        selected = set(prediction["retrieved_ids"])
        units = case["reference"]["evidence_units"]
        if units:
            evidence_coverage.append(
                sum(bool(selected & set(u["acceptable_passage_ids"])) for u in units) / len(units)
            )
            false_abstentions += prediction["execution_status"] == "OK" and not selected
        else:
            negatives.append(prediction["execution_status"] == "OK" and not selected)
    scores["selection_diagnostics"] = {
        "mean_evidence_recall_answerable": sum(evidence_coverage) / len(evidence_coverage),
        "answerable_cases": len(evidence_coverage),
        "false_abstentions": false_abstentions,
        "correct_empty_results_on_unanswerable": sum(negatives),
        "unanswerable_cases": len(negatives),
        "mean_returned_passages_all_queries": sum(len(p["retrieved_ids"]) for p in predictions.values())
        / len(predictions),
        "note": "Empty-result behavior only; no generated answer was evaluated. Errors remain in coverage denominators.",
    }
    return scores


def execute(config, base, run):
    def resolve(key):
        return (base / config[key]).resolve()

    inputs, baseline = resolve("inputs"), resolve("baseline")
    stage_config = load_retrieval_config(resolve("retrieval_config"))
    root = Path(__file__).resolve().parents[4]
    source_files = sorted((root / "src/trace_impact/retrieval").rglob("*.py"))
    files = [
        inputs / "passages.jsonl",
        inputs / "queries.jsonl",
        baseline / "predictions.jsonl",
        baseline / "diagnostics.json",
        Path(__file__),
        root / "uv.lock",
        root / "pyproject.toml",
        root / "src/trace_impact/bootstrap.py",
        *source_files,
    ]
    packages = {}
    for name in ("torch", "transformers", "tokenizers", "langchain-google-genai", "google-genai"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    contract = {
        "pipeline": stage_config.model_dump(mode="json"),
        "fingerprints": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        "runtime": {"python": platform.python_version(), "packages": packages},
    }
    manifest = run / "contract.json"
    if manifest.exists() and json.loads(manifest.read_text()) != contract:
        raise ValueError("Configuration, implementation or inputs changed; start a new experiment directory")
    save(manifest, contract)
    passages = {p["id"]: p for p in rows(inputs / "passages.jsonl")}
    queries = rows(inputs / "queries.jsonl")
    baseline_results = {p["id"]: p for p in rows(baseline / "predictions.jsonl")}
    baseline_scores = {p["id"]: p for p in json.loads((baseline / "diagnostics.json").read_text())}
    load_dotenv(resolve("environment_file"), override=False)
    ranked, selected, statuses = [], [], []
    query_directory = run / "queries"
    query_directory.mkdir(exist_ok=True)
    with create_pipeline(Settings.from_env()) as app:
        for query in queries:
            # IDs come from the trusted frozen dataset, but never use unchecked path components.
            path = query_directory / (hashlib.sha256(query["id"].encode()).hexdigest()[:20] + ".json")
            try:
                setup_ms = 0.0
                if path.exists():
                    result = RetrievalResult.model_validate_json(path.read_text())
                    cached = True
                else:
                    prediction = baseline_results[query["id"]]
                    if prediction["execution_status"] != "OK":
                        raise ValueError("Baseline retrieval failed for this case")
                    retriever = ReplayRetriever(
                        passages, prediction["retrieved_ids"], baseline_scores[query["id"]]["scores"]
                    )
                    setup_start = perf_counter()
                    service = build_retrieval_service(retriever, stage_config, app.components)
                    setup_ms = (perf_counter() - setup_start) * 1000
                    result = service.run(
                        query["query"],
                        RetrievalScope(project_id=query["project_id"], run_id=query["snapshot_id"]),
                    )
                    save(path, result.model_dump(mode="json"))
                    cached = False
                ranked.append(
                    {
                        "id": query["id"],
                        "execution_status": "OK",
                        "retrieved_ids": [s.id for s in result.ranking.scores],
                    }
                )
                selected.append(
                    {
                        "id": query["id"],
                        "execution_status": "OK",
                        "retrieved_ids": list(result.selection.ids),
                        "status": result.status,
                    }
                )
                statuses.append(
                    {
                        "id": query["id"],
                        "cached": cached,
                        "setup_ms": setup_ms,
                        "timings_ms": result.timings_ms,
                        "usage": result.ranking.usage,
                    }
                )
            except Exception as exc:
                error = {"id": query["id"], "execution_status": "ERROR", "retrieved_ids": []}
                ranked.append(error)
                selected.append(error)
                statuses.append({"id": query["id"], "error_type": type(exc).__name__})
            jsonl(run / "ranked.jsonl", ranked)
            jsonl(run / "selected.jsonl", selected)
            save(run / "execution.json", statuses)
            print(
                f"{query['id']}: {selected[-1]['execution_status']} selected={len(selected[-1]['retrieved_ids'])} ({len(selected)}/{len(queries)})",
                flush=True,
            )
    results = score_outputs(run, resolve("labels"))
    save(run / "summary.json", results)
    print(json.dumps(results, indent=2), flush=True)
    return 0 if all(p["execution_status"] == "OK" for p in selected) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    base = args.config.resolve().parent
    run = (
        args.run_dir.resolve()
        if args.run_dir
        else (base / config["output_directory"]).resolve()
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    )
    run.mkdir(parents=True, exist_ok=True)
    print(f"RUN_DIRECTORY={run}", flush=True)
    with FileLock(str(run / ".lock"), timeout=0):
        return execute(config, base, run)


if __name__ == "__main__":
    raise SystemExit(main())
