"""Run live vector retrieval from JSON inputs, then score saved outputs separately.

Usage: python -m trace_impact.evals.runners.vector_benchmark configs/evals/vector-benchmark.json
Resume: add --run-dir <the printed run directory>; successful embeddings are cached.
The retrieval function reads only inputs, never labels. This is a code boundary,
not an operating-system sandbox. No production vector or graph store is opened.
"""

import argparse
import hashlib
import json
import math
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from trace_impact import Settings, create_pipeline
from trace_impact.evals import evaluate
from trace_impact.ingestion.config import load_project
from trace_impact.ingestion.models import VectorRecord
from trace_impact.ingestion.storage.qdrant_store import QdrantVectorStore


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def checked_vector(vector, dimensions):
    if len(vector) != dimensions or not all(math.isfinite(x) for x in vector):
        raise ValueError("Invalid embedding dimensions or nonfinite values")
    if not any(vector):
        raise ValueError("Zero embedding")
    return vector


def retrieve(inputs, project_path, run, settings, store_path):
    """Input-only target adapter. JSONL predictions preserve search rank order."""
    passages = read_rows(inputs / "passages.jsonl")
    decoys = read_rows(inputs / "isolation-decoys.jsonl")
    queries = read_rows(inputs / "queries.jsonl")
    project = load_project(project_path)
    fingerprints = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (inputs / name for name in ("passages.jsonl", "queries.jsonl", "isolation-decoys.jsonl"))
    }
    with create_pipeline(settings) as pipeline:
        embeddings = pipeline.components.embeddings.resolve(project.embedding_provider)
        profile = embeddings.profile
        contract = {"inputs": fingerprints, "profile": profile.model_dump(), "target_version": 1}
        checkpoint = run / "embedding-cache.json"
        cache = (
            json.loads(checkpoint.read_text())
            if checkpoint.exists()
            else {
                "contract": contract,
                "documents": {},
                "queries": {},
                "application_embed_calls": 0,
            }
        )
        if cache["contract"] != contract:
            raise ValueError("Resume inputs/model differ; use a new run directory")
        # Deduplicate document text, including identical isolation decoys.
        unique_texts = dict.fromkeys(p["text"] for p in passages + decoys)
        pending = [
            text
            for text in unique_texts
            if hashlib.sha256(text.encode()).hexdigest() not in cache["documents"]
        ]
        for offset in range(0, len(pending), 10):
            batch = pending[offset : offset + 10]
            cache["application_embed_calls"] += 1
            save(checkpoint, cache)
            vectors = embeddings.embed_documents(batch)
            for text, vector in zip(batch, vectors, strict=True):
                cache["documents"][hashlib.sha256(text.encode()).hexdigest()] = checked_vector(
                    vector, profile.dimensions
                )
            save(checkpoint, cache)
            print(f"Document embeddings cached: {len(cache['documents'])}", flush=True)
        records = [
            VectorRecord(
                id=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([p["project_id"], p["snapshot_id"], p["id"]]))
                ),
                project_id=p["project_id"],
                run_id=p["snapshot_id"],
                profile_id=profile.id,
                chunk_id=p["id"],
                source_id=p["source_id"],
                heading="",
                text=p["text"],
                artifact_path=p["evidence"]["source_path"],
                vector=checked_vector(
                    cache["documents"][hashlib.sha256(p["text"].encode()).hexdigest()], profile.dimensions
                ),
            )
            for p in passages + decoys
        ]
        predictions, diagnostics = [], []
        with closing(QdrantVectorStore(QdrantClient(path=str(store_path)))) as store:
            store.upsert(profile, records)
            if not store.verify(profile, records):
                raise RuntimeError("Qdrant write verification failed")
            print(f"Qdrant verified {len(records)} records in isolated store", flush=True)
            for query in queries:
                try:
                    if query["id"] not in cache["queries"]:
                        cache["application_embed_calls"] += 1
                        save(checkpoint, cache)
                        cache["queries"][query["id"]] = checked_vector(
                            embeddings.embed_query(query["query"]), profile.dimensions
                        )
                        save(checkpoint, cache)
                    hits = store.search(
                        profile,
                        query["project_id"],
                        query["snapshot_id"],
                        cache["queries"][query["id"]],
                        max(query["k"]),
                    )
                    predictions.append(
                        {
                            "id": query["id"],
                            "execution_status": "OK",
                            "retrieved_ids": [hit.chunk_id for hit in hits],
                        }
                    )
                    diagnostics.append({"id": query["id"], "scores": [hit.score for hit in hits]})
                except Exception as exc:
                    predictions.append({"id": query["id"], "execution_status": "ERROR", "retrieved_ids": []})
                    # Error types are enough for this report; avoid credential-bearing exception text.
                    diagnostics.append({"id": query["id"], "error_type": type(exc).__name__})
                temp = run / "predictions.tmp"
                temp.write_text("".join(json.dumps(row) + "\n" for row in predictions), encoding="utf-8")
                temp.replace(run / "predictions.jsonl")
                save(run / "diagnostics.json", diagnostics)
                print(
                    f"Query {query['id']}: {predictions[-1]['execution_status']} ({len(predictions)}/{len(queries)})",
                    flush=True,
                )
            proofs = []
            for decoy in decoys:
                record = next(r for r in records if r.chunk_id == decoy["id"])
                own = store.search(profile, record.project_id, record.run_id, record.vector, 1)
                scoped = store.search(
                    profile,
                    passages[0]["project_id"],
                    passages[0]["snapshot_id"],
                    record.vector,
                    len(records),
                )
                proofs.append(
                    {
                        "decoy": record.chunk_id,
                        "retrievable_in_own_scope": any(h.chunk_id == record.chunk_id for h in own),
                        "excluded_from_target_scope": all(h.chunk_id != record.chunk_id for h in scoped),
                        "target_scope_count": len(scoped),
                    }
                )
            save(run / "isolation.json", proofs)
        with closing(QdrantVectorStore(QdrantClient(path=str(store_path)))) as reopened:
            persisted = reopened.verify(profile, records)
        result = {
            "profile": profile.model_dump(),
            "isolated_store_path": str(store_path),
            "input_fingerprints": fingerprints,
            "passages": len(passages),
            "decoys": len(decoys),
            "queries": len(queries),
            "completed": sum(p["execution_status"] == "OK" for p in predictions),
            "persistence_verified": persisted,
            "isolation_checks": proofs,
            "application_embed_calls_total": cache["application_embed_calls"],
            "sdk_max_attempts_per_call": settings.model_retries + 1,
            "request_timeout_seconds": settings.request_timeout,
            "requests_per_minute": project.embedding_provider.requests_per_minute,
        }
        save(run / "retrieval-run.json", result)
        if not persisted or not all(
            p["retrievable_in_own_scope"] and p["excluded_from_target_scope"] for p in proofs
        ):
            raise RuntimeError("Persistence or isolation test failed; inspect saved artifacts")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    base = args.config.resolve().parent

    def resolve(key):
        return (base / config[key]).resolve()

    run = (
        args.run_dir.resolve()
        if args.run_dir
        else resolve("output_directory")
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    )
    run.mkdir(parents=True, exist_ok=True)
    print(f"RUN_DIRECTORY={run}", flush=True)
    load_dotenv(resolve("environment_file"), override=False)
    settings = Settings.from_env().model_copy(update={"request_timeout": 45.0, "model_retries": 1})
    # Keep SQLite paths short on Windows; use a separate, per-run test directory.
    store_path = resolve("vector_cache_directory") / hashlib.sha256(str(run).encode()).hexdigest()[:12]
    result = retrieve(resolve("inputs"), resolve("project"), run, settings, store_path)
    # Only the scoring stage reads labels. It cannot alter saved predictions.
    scores = {}
    for k in config["k"]:
        evaluation = {
            "dataset": str(resolve("labels")),
            "predictions": str(run / "predictions.jsonl"),
            "output_directory": str(run / "scores"),
            "task": "ranked_retrieval",
            "k": k,
            "reference": {
                "expected_ids": None,
                "relevance_grades": "reference.relevance_grades",
                "candidate_universe": "reference.candidate_universe",
                "evidence_units": "reference.evidence_units",
                "complete": True,
            },
        }
        path = run / f"evaluation-k{k}.json"
        save(path, evaluation)
        report, summary = evaluate(path)
        scores[str(k)] = {"report": str(report), "summary": summary}
    save(
        run / "summary.json",
        {
            "retrieval": result,
            "scores": scores,
            "limits": [
                "Draft labels; no release approval.",
                "30-passage corpus; not the production index.",
                "Raw retrieval only; answer correctness and abstention not tested.",
            ],
        },
    )
    print(json.dumps(scores, indent=2), flush=True)


if __name__ == "__main__":
    main()
