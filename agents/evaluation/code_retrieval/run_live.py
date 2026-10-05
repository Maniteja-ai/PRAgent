"""Run the draft Saleor code-graph retrieval evaluation against configured live services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.config.settings import AgentSettings
from impact_agent.domain.models import ChangedFile, PullRequestRef, PullRequestSnapshot
from impact_agent.tools.knowledge.implementations.neo4j_code_graph_retriever import (
    Neo4jCodeGraphRetriever,
    Neo4jCodeGraphRetrieverFactory,
)

AGENT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = Path(__file__).with_name("golden_dataset.json")
INGESTION_ROOT = AGENT_ROOT.parent / "ingestion"
INGESTION_RUN = (
    INGESTION_ROOT / "runs" / "saleor-storefront" / "6e55bd8b924c0341e21a85d4666ca510838ed4e5"
)
RESULT_PATH = Path(__file__).with_name("results") / "latest.json"


class EvaluationCase(BaseModel):
    """One query and its independently reviewable expected direct consumers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    changed_path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    query: str = Field(min_length=1)
    expected_paths: tuple[str, ...] = Field(min_length=1)
    label_evidence: str = Field(min_length=1)


class EvaluationDataset(BaseModel):
    """Versioned retrieval evaluation cases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str
    status: Literal["DRAFT_PENDING_HUMAN_REVIEW", "HUMAN_REVIEWED"]
    indexed_revision: str
    label_basis: str
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)


class Grade(BaseModel):
    """LLM relevance assessment for one retrieved file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    path: str
    score: int = Field(ge=0, le=2)
    reason: str = Field(max_length=300)


class GradeResponse(BaseModel):
    """Strictly validated Gemini judge response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    grades: tuple[Grade, ...]


def _load_dataset(path: Path) -> EvaluationDataset:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        dataset = EvaluationDataset.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise RuntimeError(f"Could not load evaluation dataset: {path}") from error
    ids = [case.id for case in dataset.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation dataset case IDs must be unique")
    return dataset


def _code_paths(path: Path) -> frozenset[str]:
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read pinned code graph records: {path}") from error
    return frozenset(
        record["properties"]["path"]
        for record in records
        if isinstance(record, dict)
        and record.get("kind") == "CodeFile"
        and isinstance(record.get("properties"), dict)
        and isinstance(record["properties"].get("path"), str)
    )


def _make_snapshot(case: EvaluationCase) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        reference=PullRequestRef(repository="Maniteja-ai/storefront", number=0),
        title=case.title,
        description=case.query,
        base_sha="evaluation-base",
        head_sha="evaluation-head",
        files=(ChangedFile(case.changed_path, "modified", 1, 0, case.query),),
        diff=f"diff --git a/{case.changed_path} b/{case.changed_path}\n++ {case.query}",
    )


def _paths_from_evidence(
    evidence: Sequence[object], code_paths: frozenset[str], changed_path: str
) -> tuple[str, ...]:
    found: set[str] = set()
    for item in evidence:
        content = getattr(item, "content", "")
        if not isinstance(content, str) or "Connected code files:" not in content:
            continue
        related_section = content.split("Connected code files:", 1)[1].split(
            "Confirmed UI mappings:", 1
        )[0]
        for path in code_paths:
            if path != changed_path and f"{path} via " in related_section:
                found.add(path)
    return tuple(sorted(found))


def _scores(expected: frozenset[str], retrieved: frozenset[str]) -> dict[str, float | int]:
    true_positive = len(expected & retrieved)
    false_positive = len(retrieved - expected)
    false_negative = len(expected - retrieved)
    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    )
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _snippet_index(path: Path) -> dict[str, str]:
    try:
        chunks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read pinned code chunks: {path}") from error
    snippets: dict[str, str] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("metadata"), dict):
            continue
        metadata = chunk["metadata"]
        code_path = metadata.get("path")
        content = chunk.get("content")
        if isinstance(code_path, str) and isinstance(content, str):
            snippets.setdefault(code_path, content[:1200])
    return snippets


def _judge(
    settings: AgentSettings,
    environment: Mapping[str, str],
    cases: Sequence[EvaluationCase],
    retrieved_by_case: Mapping[str, Sequence[str]],
    snippets: Mapping[str, str],
) -> tuple[Grade, ...]:
    if settings.models.provider != "gemini" or not settings.models.api_key_env:
        raise RuntimeError("The configured Gemini model and API key are required for LLM judging")
    api_key = environment.get(settings.models.api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing LLM judge API key in {settings.models.api_key_env}")

    # Judge all retrieved expected files, plus up to five extras chosen by stable hash.
    # Graph results have no ranking; deterministic sampling avoids alphabetical bias.
    judge_cases: list[dict[str, object]] = []
    for case in cases:
        retrieved = set(retrieved_by_case.get(case.id, ()))
        expected = set(case.expected_paths)
        expected_matches = sorted(retrieved & expected)
        sampled_extras = sorted(
            retrieved - expected,
            key=lambda path: hashlib.sha256(f"{case.id}\0{path}".encode()).hexdigest(),
        )[:5]
        judge_paths = expected_matches + sampled_extras
        judge_cases.append(
            {
                "case_id": case.id,
                "changed_path": case.changed_path,
                "changed_code_excerpt": snippets.get(case.changed_path, "")[:1200],
                "impact_question": case.query,
                "files": [
                    {"path": path, "code_excerpt": snippets.get(path, "")[:1200]}
                    for path in judge_paths
                ],
            }
        )
    system_prompt = (
        "Judge whether each retrieved source file is relevant to the code-impact question. "
        "Use the changed file, stated question, and code excerpt. Score 2 when the file is a "
        "direct functional consumer or necessary impact path, 1 when contextually related but "
        "not clearly impacted, and 0 when unrelated. Do not assume relevance just because a "
        "file is in the same folder or shares a generic utility. Return one grade for every input "
        "file, preserve the exact case_id and path, and give a short evidence-based reason."
    )
    request_body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(judge_cases)}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "grades": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "case_id": {"type": "STRING"},
                                "path": {"type": "STRING"},
                                "score": {"type": "INTEGER"},
                                "reason": {"type": "STRING"},
                            },
                            "required": ["case_id", "path", "score", "reason"],
                        },
                    }
                },
                "required": ["grades"],
            },
        },
    }
    url = (
        f"{settings.models.api_url.rstrip('/')}/models/"
        f"{quote(settings.models.model, safe='')}:generateContent"
    )
    try:
        response = httpx.post(
            url,
            headers={"x-goog-api-key": api_key},
            json=request_body,
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        text = "".join(
            part.get("text", "")
            for part in payload["candidates"][0]["content"]["parts"]
            if isinstance(part, dict)
        )
        return GradeResponse.model_validate_json(text).grades
    except httpx.HTTPStatusError as error:
        raise RuntimeError(f"LLM judge returned HTTP {error.response.status_code}") from error
    except (
        httpx.RequestError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise RuntimeError("LLM judge request failed validation or transport") from error


def _micro_scores(results: Sequence[Mapping[str, object]]) -> dict[str, float | int]:
    totals = {"true_positive": 0, "false_positive": 0, "false_negative": 0}
    for result in results:
        score_data = result.get("scores")
        if not isinstance(score_data, Mapping):
            raise ValueError("Evaluation case has no validated score object")
        for name in totals:
            value = score_data.get(name)
            if not isinstance(value, int):
                raise ValueError(f"Evaluation score '{name}' is not an integer")
            totals[name] += value
    tp = totals["true_positive"]
    fp = totals["false_positive"]
    fn = totals["false_negative"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        **totals,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--judge-existing",
        action="store_true",
        help="Re-run only the LLM judge on the saved results without querying Neo4j.",
    )
    arguments = parser.parse_args()
    dataset = _load_dataset(DATASET_PATH)
    settings: AgentSettings = JsonConfigLoader().load(AGENT_ROOT / "config" / "default")
    if settings.graph_database.indexed_revision != dataset.indexed_revision:
        raise RuntimeError("Dataset revision and configured Neo4j revision do not match")

    load_dotenv(AGENT_ROOT / ".env", override=False)
    environment = dict(os.environ)
    code_paths = _code_paths(INGESTION_RUN / "graph_records.json")
    snippets = _snippet_index(INGESTION_RUN / "code_chunks.json")
    unknown_paths = {
        path
        for case in dataset.cases
        for path in (case.changed_path, *case.expected_paths)
        if path not in code_paths
    }
    if unknown_paths:
        raise ValueError(
            f"Dataset contains paths absent from the pinned graph: {sorted(unknown_paths)}"
        )
    if arguments.judge_existing:
        try:
            report = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not load saved evaluation results: {RESULT_PATH}") from error
        saved_cases = report.get("cases")
        if not isinstance(saved_cases, list):
            raise ValueError("Saved evaluation report has no cases list")
        retrieved_by_case = {
            item["case_id"]: tuple(item["retrieved_paths"])
            for item in saved_cases
            if isinstance(item, dict)
            and isinstance(item.get("case_id"), str)
            and isinstance(item.get("retrieved_paths"), list)
        }
        judged_cases = tuple(case for case in dataset.cases if case.id in retrieved_by_case)
        grades = _judge(settings, environment, judged_cases, retrieved_by_case, snippets)
        report["llm_judge"] = _judge_summary(
            dataset, settings.models.model, grades, retrieved_by_case
        )
        RESULT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"LLM judge refreshed from saved retrieval results: {RESULT_PATH}")
        return 0
    retriever: Neo4jCodeGraphRetriever | None = Neo4jCodeGraphRetrieverFactory.create(
        settings.graph_database,
        settings.knowledge.max_evidence,
        environment,
        settings.models,
        settings.runtime.request_timeout_seconds,
    )
    if retriever is None:
        raise RuntimeError("Neo4j graph retrieval is disabled in graph_database.json")

    case_results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    retrieved_by_case: dict[str, tuple[str, ...]] = {}
    consecutive_failures = 0
    skipped_case_ids: list[str] = []
    try:
        for index, case in enumerate(dataset.cases):
            print(
                f"[{index + 1}/{len(dataset.cases)}] querying Neo4j for {case.id} ...", flush=True
            )
            try:
                evidence = retriever.retrieve(_make_snapshot(case))
            except Exception as error:
                failures.append({"case_id": case.id, "error_type": type(error).__name__})
                consecutive_failures += 1
                print(f"  failed: {type(error).__name__}", flush=True)
                if consecutive_failures >= 2:
                    skipped_case_ids.extend(item.id for item in dataset.cases[index + 1 :])
                    print("Stopping after two consecutive retrieval failures.", flush=True)
                    break
                continue
            consecutive_failures = 0
            retrieved = _paths_from_evidence(evidence, code_paths, case.changed_path)
            retrieved_by_case[case.id] = retrieved
            expected = frozenset(case.expected_paths)
            actual = frozenset(retrieved)
            case_results.append(
                {
                    "case_id": case.id,
                    "title": case.title,
                    "changed_path": case.changed_path,
                    "expected_paths": sorted(expected),
                    "retrieved_paths": list(retrieved),
                    "missing_paths": sorted(expected - actual),
                    "unexpected_paths": sorted(actual - expected),
                    "scores": _scores(expected, actual),
                    "label_evidence": case.label_evidence,
                }
            )
            case_scores = case_results[-1]["scores"]
            print(
                f"  retrieved={len(retrieved)} precision={case_scores['precision']} "
                f"recall={case_scores['recall']}",
                flush=True,
            )
    finally:
        retriever.close()

    judged_cases = tuple(case for case in dataset.cases if case.id in retrieved_by_case)
    judged_files = any(retrieved_by_case.values())
    grades = (
        _judge(settings, environment, judged_cases, retrieved_by_case, snippets)
        if judged_files
        else ()
    )
    case_count = len(case_results)
    judge_summary = _judge_summary(dataset, settings.models.model, grades, retrieved_by_case)
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset.dataset_id,
        "dataset_status": dataset.status,
        "indexed_revision": dataset.indexed_revision,
        "retriever": "Neo4jCodeGraphRetriever + configured Gemini Cypher planner",
        "label_basis": dataset.label_basis,
        "case_count": case_count,
        "evaluation_status": "COMPLETED" if not failures and not skipped_case_ids else "PARTIAL",
        "failed_cases": failures,
        "skipped_case_ids": skipped_case_ids,
        "micro_scores": _micro_scores(case_results),
        "macro_scores": {
            metric: round(
                sum(
                    float(result_scores[metric])
                    for result in case_results
                    if isinstance((result_scores := result.get("scores")), Mapping)
                )
                / case_count,
                4,
            )
            for metric in ("precision", "recall", "f1")
        },
        "llm_judge": judge_summary,
        "cases": case_results,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "dataset_id",
                    "dataset_status",
                    "evaluation_status",
                    "case_count",
                    "failed_cases",
                    "skipped_case_ids",
                    "micro_scores",
                    "macro_scores",
                )
            },
            indent=2,
        )
    )
    print(f"LLM judge: {len(grades)} files scored by {settings.models.model}")
    print(f"Full report: {RESULT_PATH}")
    return 0


def _judge_summary(
    dataset: EvaluationDataset,
    model: str,
    grades: Sequence[Grade],
    retrieved_by_case: Mapping[str, Sequence[str]],
) -> dict[str, object]:
    case_by_id = {case.id: case for case in dataset.cases}
    expected_count = 0
    expected_relevant = 0
    extra_count = 0
    extra_relevant = 0
    for grade in grades:
        case = case_by_id.get(grade.case_id)
        if case is None or grade.path not in retrieved_by_case.get(grade.case_id, ()):
            continue
        if grade.path in case.expected_paths:
            expected_count += 1
            expected_relevant += int(grade.score == 2)
        else:
            extra_count += 1
            extra_relevant += int(grade.score == 2)
    grade_records = [
        {
            **grade.model_dump(),
            "matches_expected_path_label": grade.path in case_by_id[grade.case_id].expected_paths,
        }
        for grade in grades
        if grade.case_id in case_by_id
    ]
    grade_count = len(grades)
    return {
        "model": model,
        "graded_files": grade_count,
        "mean_score_0_to_2": round(sum(grade.score for grade in grades) / grade_count, 4)
        if grade_count
        else 0.0,
        "expected_paths_judged": expected_count,
        "expected_paths_judged_relevant_fraction": round(expected_relevant / expected_count, 4)
        if expected_count
        else None,
        "sampled_extras_judged": extra_count,
        "sampled_extras_judged_relevant_fraction": round(extra_relevant / extra_count, 4)
        if extra_count
        else None,
        "grades": grade_records,
        "note": (
            "LLM grades are a diagnostic sample: all retrieved expected paths plus up to five "
            "deterministically sampled extra paths per case. They do not define precision/recall."
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
