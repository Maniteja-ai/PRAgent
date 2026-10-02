"""Collect and extract are independently resumable stages; no database or LLM required to collect."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .documents import NORMALIZER_VERSION, snapshot_source
from .extraction import PROMPT_VERSION, SYSTEM_PROMPT, Extractor, deduplicate, validate_candidate
from .models import Corpus, Extraction, ExtractionRun, load_project, stable_id


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def collect(config: Path, output_root: Path) -> tuple[Path, Corpus]:
    project = load_project(config)
    run_id = uuid.uuid4().hex
    run_dir = output_root / project.project_id / run_id
    run_dir.mkdir(parents=True)
    snapshots, chunks, errors = [], [], []
    for source in project.sources:
        try:
            snapshot, source_chunks = snapshot_source(source, project, config.parent, run_dir)
            snapshots.append(snapshot)
            chunks.extend(source_chunks)
        except Exception as exc:
            # Record failure explicitly; never silently represent a partial corpus as complete.
            errors.append({"source_id": source.id, "error_type": type(exc).__name__})
    corpus = Corpus(
        run_id=run_id,
        project=project,
        created_at=datetime.now(UTC).isoformat(),
        config_sha256=hashlib.sha256(project.model_dump_json().encode()).hexdigest(),
        snapshots=snapshots,
        chunks=chunks,
        errors=errors,
    )
    write_json(run_dir / "corpus.json", corpus.model_dump())
    write_json(
        run_dir / "inventory.json",
        {
            "project_id": project.project_id,
            "run_id": run_id,
            "normalizer_version": NORMALIZER_VERSION,
            "status": "PARTIAL" if errors else "COLLECTED",
            "sources_requested": len(project.sources),
            "sources_collected": len(snapshots),
            "chunks": len(chunks),
            "characters": sum(len(c.text) for c in chunks),
            "oversized_chunks": [c.id for c in chunks if c.oversized],
            "errors": errors,
            "sources": [
                {
                    "source_id": s.source_id,
                    "version": s.version,
                    "normalized_sha256": s.normalized_sha256,
                    "chunks": sum(c.snapshot_id == s.id for c in chunks),
                }
                for s in snapshots
            ],
        },
    )
    return run_dir, corpus


def extract(run_dir: Path, extractor: Extractor, max_chunks: int = 100) -> ExtractionRun:
    corpus = Corpus.model_validate_json((run_dir / "corpus.json").read_text(encoding="utf-8"))
    if corpus.errors:
        raise ValueError("Source collection is partial; fix the sources and collect again before extraction")
    snapshots = {s.id: s for s in corpus.snapshots}
    run = ExtractionRun(
        id=uuid.uuid4().hex,
        corpus_run_id=corpus.run_id,
        project_id=corpus.project.project_id,
        provider=extractor.provider,
        model=extractor.model,
        prompt_version=PROMPT_VERSION,
        created_at=datetime.now(UTC).isoformat(),
        status="PARTIAL",
        processed_chunk_ids=[],
        no_requirement_chunks={},
        errors=[],
        requirements=[],
    )
    cache = run_dir / "extraction-cache"
    cache.mkdir(exist_ok=True)
    for chunk in corpus.chunks[:max_chunks]:
        snapshot = snapshots[chunk.snapshot_id]
        cache_key = stable_id(
            extractor.provider,
            extractor.model,
            SYSTEM_PROMPT,
            json.dumps(Extraction.model_json_schema(), sort_keys=True),
            corpus.config_sha256,
            snapshot.authority,
            chunk.id,
        )
        path = cache / f"{cache_key}.json"
        try:
            if path.exists():
                result = Extraction.model_validate_json(path.read_text(encoding="utf-8"))
            else:
                result = extractor.extract(chunk, snapshot, corpus.project.scope)
                if not result.requirements and not result.no_requirement_reason:
                    raise ValueError("Empty extraction needs an explanation")
                write_json(path, result.model_dump())
            for candidate in result.requirements:
                run.requirements.append(validate_candidate(candidate, chunk, snapshot, run.project_id))
            run.processed_chunk_ids.append(chunk.id)
            if not result.requirements:
                run.no_requirement_chunks[chunk.id] = result.no_requirement_reason or "No requirement"
        except Exception as exc:
            run.errors.append({"chunk_id": chunk.id, "error_type": type(exc).__name__})
        write_json(run_dir / "extractions" / f"{run.id}.json", run.model_dump())
        write_json(run_dir / "extraction.json", run.model_dump())
    if len(run.processed_chunk_ids) == len(corpus.chunks) and not run.errors:
        run.status = "COMPLETE"
    run.requirements = deduplicate(run.requirements)
    write_json(run_dir / "extractions" / f"{run.id}.json", run.model_dump())
    write_json(run_dir / "extraction.json", run.model_dump())
    write_json(
        run_dir / "review.json",
        {
            "extraction_run_id": run.id,
            "status": run.status,
            "requirements": [
                r.model_dump() for r in run.requirements if r.validation != "GROUNDED_CANDIDATE"
            ],
            "note": "Quote grounding does not prove semantic entailment. Review a sample of grounded candidates too.",
        },
    )
    return run
