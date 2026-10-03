from typing import cast

import pytest
from pydantic import ValidationError

from trace_coordinator.config import CoordinatorFile, GeminiProvider
from trace_coordinator.domain.contracts import AnalysisReportPayload
from trace_coordinator.domain.models import ReportNarrative
from trace_coordinator.presentation.response_formatter import (
    FallbackResponseFormatter,
    LlmResponseFormatter,
    TemplateResponseFormatter,
)


def report() -> AnalysisReportPayload:
    return cast(
        AnalysisReportPayload,
        {
            "status": "COMPLETED_WITH_GAPS",
            "completeness": "PARTIAL",
            "verification": "NOT_EXECUTED",
            "request": {
                "schema_version": 1,
                "project_id": "store",
                "repository": "owner/repository",
                "pull_request": 7,
                "question": "Which checkout flow may change?",
            },
            "findings": [
                {
                    "title": "Voucher total may change",
                    "explanation": "The changed calculation feeds the checkout total.",
                    "evidence_ids": ["diff-1", "graph-1"],
                    "checks": ["Apply a valid voucher and inspect the total."],
                    "classification": "POTENTIAL_IMPACT",
                }
            ],
            "evidence": {
                "diff-1": {
                    "id": "diff-1",
                    "project_id": "store",
                    "kind": "diff",
                    "summary": "secret source text that must not be sent to the report writer",
                    "source": "git",
                    "metadata": {},
                }
            },
            "gaps": ["A human did not approve behavioral verification."],
            "behavior_verification": {"status": "NOT_EXECUTED", "checks": []},
            "human_review": {
                "policy": "non_blocking",
                "status": "NOT_ANSWERED",
                "requests": [
                    {
                        "kind": "analysis",
                        "question": "private reviewer question",
                        "status": "NOT_ANSWERED",
                        "answer": None,
                    }
                ],
                "follow_up_verification_allowed": True,
            },
            "tool_usage": [],
        },
    )


def test_llm_narrative_improves_prose_without_replacing_report_facts():
    class Structured:
        def invoke(self, _messages):
            return {
                "executive_summary": (
                    "This PR may affect the checkout voucher total. Verification is pending."
                ),
                "findings": [
                    {
                        "position": 0,
                        "explanation": (
                            "The updated calculation may change the total shown after a voucher is applied."
                        ),
                    }
                ],
            }

    class Client:
        def with_structured_output(self, _schema, method):
            assert method == "json_schema"
            return Structured()

    formatter = LlmResponseFormatter(GeminiProvider(provider="gemini", model="example"), client=Client())
    rendered = formatter.format(report())
    assert "This PR may affect the checkout voucher total" in rendered
    assert "The updated calculation may change the total" in rendered
    assert "COMPLETED_WITH_GAPS" in rendered
    assert "NOT_EXECUTED" in rendered
    assert "diff-1, graph-1" in rendered
    assert "Not executed: Apply a valid voucher" in rendered
    assert "private reviewer question" in rendered


def test_primary_formatter_failure_falls_back_to_template():
    class BrokenFormatter:
        def format(self, _report: AnalysisReportPayload) -> str:
            raise TimeoutError

    rendered = FallbackResponseFormatter(BrokenFormatter(), TemplateResponseFormatter()).format(report())
    assert "The analysis identified 1 potential impact finding" in rendered
    assert "The changed calculation feeds the checkout total" in rendered
    assert "diff-1, graph-1" in rendered


def test_llm_formatter_sends_only_minimal_validated_report_data():
    calls: list[object] = []

    class Structured:
        def invoke(self, messages):
            calls.extend(messages)
            return {
                "executive_summary": "One potential checkout impact was identified.",
                "findings": [{"position": 0, "explanation": "Voucher totals may be affected."}],
            }

    class Client:
        def with_structured_output(self, schema, method):
            assert schema is ReportNarrative and method == "json_schema"
            return Structured()

    formatter = LlmResponseFormatter(
        GeminiProvider(provider="gemini", model="example"),
        client=Client(),
    )
    rendered = formatter.format(report())
    assert "Voucher totals may be affected" in rendered
    serialized_messages = str(calls)
    assert "secret source text" not in serialized_messages
    assert "private reviewer question" not in serialized_messages
    assert "current_explanation" in serialized_messages


def test_llm_formatter_rejects_missing_finding_rewrite():
    class Structured:
        def invoke(self, _messages):
            return {"executive_summary": "Summary", "findings": []}

    class Client:
        def with_structured_output(self, _schema, _method=None, **_kwargs):
            return Structured()

    formatter = LlmResponseFormatter(
        GeminiProvider(provider="gemini", model="example"),
        client=Client(),
    )
    with pytest.raises(ValueError, match="one ordered explanation"):
        formatter.format(report())


def test_fixture_model_cannot_enable_llm_response_formatter():
    with pytest.raises(ValidationError, match="live model provider"):
        CoordinatorFile.model_validate(
            {
                "tool_fixture_file": "tools.json",
                "model": {"provider": "fixture", "file": "model.json"},
                "response_formatter": {"provider": "llm"},
            }
        )
