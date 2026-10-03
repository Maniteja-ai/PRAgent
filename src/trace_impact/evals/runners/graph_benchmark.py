"""Run the frozen graph contract against real Neo4j, then score labels separately.

python -m trace_impact.evals.runners.graph_benchmark
Writes an isolated immutable fixture graph. Does not clear the shared database.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from trace_impact import Settings, create_pipeline
from trace_impact.evals import evaluate
from trace_impact.retrieval.graph.neo4j_reader import Neo4jGraphReader
from trace_impact.retrieval.graph.service import ImpactRetriever
from trace_impact.shared.graph_models import GraphSnapshot

ROOT = Path(__file__).resolve().parents[4]
DATASET = ROOT / "evaluation/datasets/saleor-retrieval-v0.1"


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def check_witnesses(snapshot, query, result):
    """Validate returned path evidence without consulting expected answer sets."""
    nodes = {n.id: n for n in snapshot.nodes}
    edges = {e.id: e for e in snapshot.edges}
    targets = set(result.symbol_ids + result.ui_ids + result.flow_ids + result.requirement_ids)
    paths = {p.target_id: p for p in result.witness_paths}
    assert targets <= set(paths), "A retrieved target has no witness"
    scope = query["scope"]
    roots = set(query["changed_symbol_ids"])
    if query.get("symbol_lookup"):
        roots = {
            n.id
            for n in snapshot.nodes
            if n.name == query["symbol_lookup"]["name"]
            and n.project_id == scope["project_id"]
            and n.revision == scope["revision"]
        }
    for witness in result.witness_paths:
        assert witness.node_ids[0] in roots and witness.node_ids[-1] == witness.target_id
        assert len(witness.node_ids) == len(witness.edge_ids) + 1
        assert len(set(witness.node_ids)) == len(witness.node_ids), "Cyclic witness"
        dependency_hops = 0
        for node_id in witness.node_ids:
            assert (
                nodes[node_id].project_id == scope["project_id"]
                and nodes[node_id].revision == scope["revision"]
            )
        for a, b, edge_id in zip(witness.node_ids, witness.node_ids[1:], witness.edge_ids, strict=False):
            edge = edges[edge_id]
            assert edge.status == "CONFIRMED"
            reverse = edge.type in {"DEPENDS_ON", "CONTAINS"}
            assert (edge.source, edge.target) == ((b, a) if reverse else (a, b))
            dependency_hops += edge.type == "DEPENDS_ON"
        assert dependency_hops <= scope["max_dependency_hops"]


def main():
    load_dotenv(ROOT / ".env", override=False)
    run = (
        ROOT
        / "artifacts/evaluation-runs/graph"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    )
    run.mkdir(parents=True)
    print(f"RUN_DIRECTORY={run}", flush=True)
    fixture_file = DATASET / "inputs/graph/fixture.json"
    queries_file = DATASET / "inputs/graph/queries.jsonl"
    snapshot = GraphSnapshot.model_validate_json(fixture_file.read_text(encoding="utf-8"))
    snapshot = snapshot.model_copy(update={"id": "integration-impact-" + uuid.uuid4().hex})
    queries = [json.loads(line) for line in queries_file.read_text(encoding="utf-8").splitlines()]
    files = [
        fixture_file,
        queries_file,
        Path(__file__),
        *sorted((ROOT / "src/trace_impact/retrieval/graph").glob("*.py")),
        *sorted((ROOT / "src/trace_impact/ingestion/code").glob("*.py")),
        ROOT / "src/trace_impact/ingestion/storage/neo4j_code_store.py",
        ROOT / "src/trace_impact/shared/graph_models.py",
    ]
    save(
        run / "contract.json",
        {
            "graph_id": snapshot.id,
            "origin": snapshot.origin,
            "sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        },
    )
    save(run / "snapshot.json", snapshot.model_dump(mode="json"))
    predictions, execution = [], []
    with create_pipeline(Settings.from_env()) as app:
        first = app.publish_code_graph(snapshot)
        assert app.publish_code_graph(snapshot) == first, "Repeated publication was not idempotent"
        save(run / "publication.json", first)
        try:
            app.publish_code_graph(snapshot.model_copy(update={"origin": "changed-content"}))
        except ValueError:
            immutable = True
        else:
            raise AssertionError("An immutable snapshot was overwritten")
        store = app.components.graphs.resolve("neo4j")
        retriever = ImpactRetriever(Neo4jGraphReader(store.driver, store.database, snapshot.id))
        for query in queries:
            start = perf_counter()
            try:
                result = retriever.retrieve(query)
                check_witnesses(snapshot, query, result)
                prediction = {"id": query["id"], "execution_status": "OK", **result.model_dump(mode="json")}
            except Exception as exc:
                prediction = {
                    "id": query["id"],
                    "execution_status": "ERROR",
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "ui_ids": [],
                    "flow_ids": [],
                    "requirement_ids": [],
                }
            predictions.append(prediction)
            execution.append(
                {
                    "id": query["id"],
                    "seconds": perf_counter() - start,
                    "execution_status": prediction["execution_status"],
                }
            )
            (run / "predictions.jsonl").write_text(
                "".join(json.dumps(p) + "\n" for p in predictions), encoding="utf-8"
            )
            save(run / "execution.json", execution)
            print(f"{query['id']}: {prediction['execution_status']} {prediction['status']}", flush=True)
    # Only scoring receives the reference labels, after all retrieval has completed.
    reports = {}
    for field in ("ui_ids", "flow_ids", "requirement_ids"):
        config = {
            "dataset": str(DATASET / "labels/graph-cases.jsonl"),
            "predictions": str(run / "predictions.jsonl"),
            "output_directory": str(run / "scores"),
            "task": "set_retrieval",
            "reference": {
                "expected_ids": f"reference.{field}",
                "expected_status": "reference.status",
                "complete": True,
            },
            "prediction": {"items": field},
        }
        path = run / f"{field}-evaluation.json"
        save(path, config)
        report, summary = evaluate(path)
        reports[field] = {"report": str(report), "summary": summary}
    reports["checks"] = {
        "idempotent_publication": True,
        "immutable_snapshot": immutable,
        "witness_validation": "PASS"
        if all(p["execution_status"] == "OK" for p in predictions)
        else "INCOMPLETE",
        "fixture_only": True,
    }
    save(run / "summary.json", reports)
    print(json.dumps(reports, indent=2), flush=True)
    return int(any(p["execution_status"] != "OK" for p in predictions))


if __name__ == "__main__":
    raise SystemExit(main())
