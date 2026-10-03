"""Live decision-model evaluation with deterministic safety and grounding checks."""

import hashlib
import time
from pathlib import Path
from statistics import median
from typing import Literal

from pydantic import Field

from trace_coordinator.application.interfaces import DecisionModel
from trace_coordinator.config import GeminiProvider, OpenAIProvider
from trace_coordinator.domain.contracts import GuardrailAuditPayload, JsonObject, as_json_object
from trace_coordinator.domain.errors import FailureCode, ToolFailure
from trace_coordinator.domain.models import Evidence, Record
from trace_coordinator.infrastructure.adapters.langchain_model import LangChainModel
from trace_coordinator.security.guardrails import GuardrailEngine, GuardrailPolicy, findings


class LiveExpected(Record):
    provider_called: bool
    action: Literal["tool", "review", "finish"] | None = None
    minimum_findings: int = Field(default=0, ge=0, le=30)
    required_citation_ids: tuple[str, ...] = ()
    forbidden_citation_ids: tuple[str, ...] = ()
    guardrail_status: Literal["PASSED", "SANITIZED", "BLOCKED"]


class LiveLLMCase(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    question: str = Field(min_length=1, max_length=4000)
    evidence: tuple[Evidence, ...] = Field(default=(), max_length=20)
    expected: LiveExpected


class LiveLLMEvaluationConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100)
    env_file: str | None = None
    model: GeminiProvider | OpenAIProvider = Field(discriminator="provider")
    guardrails: GuardrailPolicy = Field(default_factory=GuardrailPolicy)
    max_provider_calls: int = Field(default=5, ge=1, le=5)
    minimum_case_pass_rate: float = Field(default=1, ge=0, le=1)
    minimum_structured_output_rate: float = Field(default=1, ge=0, le=1)
    minimum_grounded_finding_rate: float = Field(default=1, ge=0, le=1)
    cases: tuple[LiveLLMCase, ...] = Field(min_length=1, max_length=20)


def _context(case: LiveLLMCase, evidence: tuple[Evidence, ...]) -> JsonObject:
    return as_json_object(
        {
            "request": {
                "schema_version": 1,
                "project_id": "live-llm-evaluation",
                "repository": "saleor/storefront",
                "pull_request": 1199,
                "question": case.question,
            },
            "round": 1,
            "evidence": {item.id: item.model_dump(mode="json") for item in evidence},
            "gaps": [],
            "human_answers": [],
            "tools": [],
            "per_tool_limit": 5,
            "calls_already_used": [],
            "validation_errors": [],
            "previous_findings": [],
            "phase": "analysis",
            "ui_exploration": {"enabled": False, "status": "DISABLED", "steps": 0},
        }
    )


def evaluate_live_llm(
    path: str | Path,
    output_directory: str | Path,
    *,
    model: DecisionModel | None = None,
) -> JsonObject:
    source = Path(path).resolve()
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    config = LiveLLMEvaluationConfig.model_validate_json(raw)
    if config.env_file:
        from dotenv import load_dotenv

        load_dotenv(source.parent / config.env_file, override=False)
    engine = GuardrailEngine(config.guardrails)
    owned = model is None
    selected_model: DecisionModel = model or LangChainModel(config.model)
    provider_calls = structured = grounded_findings = total_findings = 0
    results: list[JsonObject] = []
    called_cases = 0
    cases_passed = 0
    sensitive_output_leaks = 0
    latencies: list[float] = []
    try:
        for case in config.cases:
            called = False
            decision = None
            error = None
            input_audit: GuardrailAuditPayload = {"status": "BLOCKED", "counts": {}}
            started = time.perf_counter()
            try:
                engine.validate_user_text(case.question, field="evaluation request")
                safe, input_audit = engine.prepare_model_input(_context(case, case.evidence))
                if provider_calls >= config.max_provider_calls:
                    raise ToolFailure("Live LLM evaluation call limit reached")
                provider_calls += 1
                called = True
                decision, _ = engine.validate_model_output(selected_model.decide(safe))
                structured += 1
            except ToolFailure as exc:
                error = exc.code.value if exc.code != FailureCode.UNSPECIFIED else type(exc).__name__
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            if called:
                latencies.append(elapsed_ms)
                called_cases += 1
            evidence_ids = {item.id for item in case.evidence}
            cited = {
                citation for item in (decision.findings if decision else ()) for citation in item.evidence_ids
            }
            decision_findings = decision.findings if decision is not None else ()
            case_grounded = bool(decision is not None) and all(
                set(item.evidence_ids) <= evidence_ids
                and any(ref.startswith("diff") for ref in item.evidence_ids)
                and any(not ref.startswith("diff") for ref in item.evidence_ids)
                for item in decision_findings
            )
            finding_count = len(decision.findings) if decision else 0
            total_findings += finding_count
            grounded_findings += finding_count if case_grounded else 0
            serialized = decision.model_dump_json() if decision else ""
            leaks = {
                name: count for name, count in findings(serialized).items() if name != "prompt_injection"
            }
            actual_guardrail = (
                "BLOCKED"
                if error == FailureCode.GUARDRAIL_BLOCKED.value and not called
                else input_audit["status"]
            )
            passed = (
                called == case.expected.provider_called
                and actual_guardrail == case.expected.guardrail_status
                and (case.expected.action is None or (decision and decision.action == case.expected.action))
                and finding_count >= case.expected.minimum_findings
                and set(case.expected.required_citation_ids) <= cited
                and not (set(case.expected.forbidden_citation_ids) & cited)
                and (not decision or cited <= evidence_ids)
                and (finding_count == 0 or case_grounded)
                and not leaks
            )
            cases_passed += int(bool(passed))
            sensitive_output_leaks += int(bool(leaks))
            results.append(
                as_json_object(
                    {
                        "id": case.id,
                        "passed": bool(passed),
                        "provider_called": called,
                        "latency_ms": elapsed_ms if called else None,
                        "guardrail": input_audit,
                        "actual_guardrail_status": actual_guardrail,
                        "error": error,
                        "decision": decision.model_dump(mode="json") if decision else None,
                        "citations_valid": not decision or cited <= evidence_ids,
                        "grounded": case_grounded,
                        "sensitive_output_findings": leaks,
                    }
                )
            )
    finally:
        if owned and isinstance(selected_model, LangChainModel):
            selected_model.close()
    case_pass_rate = cases_passed / len(results)
    structured_rate = structured / called_cases if called_cases else 1.0
    grounded_rate = grounded_findings / total_findings if total_findings else 1.0
    passed = (
        case_pass_rate >= config.minimum_case_pass_rate
        and structured_rate >= config.minimum_structured_output_rate
        and grounded_rate >= config.minimum_grounded_finding_rate
        and provider_calls <= config.max_provider_calls
    )
    return as_json_object(
        {
            "schema_version": 1,
            "status": "PASSED" if passed else "FAILED",
            "dataset": {
                "id": config.id,
                "path": str(source),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "model": {"provider": config.model.provider, "name": config.model.model},
            "guardrail_policy_fingerprint": engine.version.split(":", 1)[1],
            "metrics": {
                "cases_passed": cases_passed,
                "cases_total": len(results),
                "case_pass_rate": case_pass_rate,
                "provider_calls": provider_calls,
                "structured_output_rate": structured_rate,
                "grounded_finding_rate": grounded_rate,
                "sensitive_output_leaks": sensitive_output_leaks,
                "latency_ms_median": round(median(latencies), 2) if latencies else None,
                "latency_ms_max": max(latencies) if latencies else None,
            },
            "thresholds": {
                "case_pass_rate": config.minimum_case_pass_rate,
                "structured_output_rate": config.minimum_structured_output_rate,
                "grounded_finding_rate": config.minimum_grounded_finding_rate,
                "max_provider_calls": config.max_provider_calls,
            },
            "cases": results,
        }
    )


def live_llm_markdown(report: JsonObject) -> str:
    metrics = as_json_object(report["metrics"])
    model = as_json_object(report["model"])
    raw_cases = report["cases"]
    if not isinstance(raw_cases, list):
        raise ValueError("Live LLM report cases must be a list")
    cases = [as_json_object(item) for item in raw_cases]
    lines = [
        "# Live LLM evaluation",
        "",
        f"Status: {report['status']}",
        "",
        f"Model: {model['provider']} / {model['name']}",
        "",
        "| Metric | Result |",
        "| --- | ---: |",
        f"| Cases passed | {metrics['cases_passed']}/{metrics['cases_total']} |",
        f"| Provider calls | {metrics['provider_calls']} |",
        f"| Structured output rate | {metrics['structured_output_rate']:.3f} |",
        f"| Grounded finding rate | {metrics['grounded_finding_rate']:.3f} |",
        f"| Sensitive output leaks | {metrics['sensitive_output_leaks']} |",
        f"| Median live latency | {metrics['latency_ms_median']} ms |",
        f"| Maximum live latency | {metrics['latency_ms_max']} ms |",
        "",
        "| Case | Result | Provider | Guardrail | Latency |",
        "| --- | --- | --- | --- | ---: |",
    ]
    lines.extend(
        f"| {item['id']} | {'PASS' if item['passed'] else 'FAIL'} | "
        f"{'called' if item['provider_called'] else 'blocked'} | {item['actual_guardrail_status']} | "
        f"{item['latency_ms'] if item['latency_ms'] is not None else '-'} |"
        for item in cases
    )
    return "\n".join(lines) + "\n"


def live_llm_schema() -> JsonObject:
    return as_json_object(
        {
            **LiveLLMEvaluationConfig.model_json_schema(),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Live LLM evaluation",
        }
    )
