"""Fail-closed request, evidence, citation, and sensitive-output checks."""

import hashlib
import hmac
import re
from dataclasses import replace

from impact_agent.config.validation.guardrails import GuardrailConfig
from impact_agent.domain.models import Decision, Evidence, PullRequestRef
from impact_agent.guardrails.interface.guardrail import Guardrail

_SENSITIVE_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
)
_INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions"),
    re.compile(r"(?i)disregard\s+(all\s+)?(previous|prior|above)\s+instructions"),
    re.compile(r"(?i)reveal\s+(the\s+)?(system prompt|secrets?|api keys?)"),
)
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class BasicGuardrail(Guardrail):
    """Enforce local policy without sending sensitive content to another model."""

    def __init__(self, config: GuardrailConfig) -> None:
        self._config = config

    def validate_request(self, reference: PullRequestRef) -> None:
        if not _REPOSITORY_PATTERN.fullmatch(reference.repository):
            raise ValueError("Pull request repository must be owner/name")
        if any(part in {".", ".."} for part in reference.repository.split("/")):
            raise ValueError("Pull request repository contains an invalid path segment")
        if reference.number < 1:
            raise ValueError("Pull request number must be positive")

    def validate_evidence(self, evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        if len({item.evidence_id for item in evidence}) != len(evidence):
            raise ValueError("Evidence IDs must be unique within a run")
        total_characters = sum(len(item.content) for item in evidence)
        if total_characters > self._config.max_evidence_characters:
            raise ValueError("Evidence exceeds the configured total character limit")

        checked: list[Evidence] = []
        for item in evidence:
            content_hash = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(content_hash, item.content_sha256.lower()):
                raise ValueError(f"Evidence hash does not match content: {item.evidence_id}")
            if self._config.block_untrusted_instructions and any(
                pattern.search(item.content) for pattern in _INJECTION_PATTERNS
            ):
                raise ValueError(
                    f"Evidence contains a blocked instruction pattern: {item.evidence_id}"
                )
            content = (
                self._redact(item.content) if self._config.redact_sensitive_values else item.content
            )
            checked.append(replace(item, content=content, content_sha256=self._hash(content)))
        return tuple(checked)

    def validate_decision(self, decision: Decision, evidence: tuple[Evidence, ...]) -> Decision:
        allowed_ids = {item.evidence_id for item in evidence}
        if self._config.require_evidence_for_findings:
            for finding in decision.findings:
                if not finding.evidence_ids:
                    raise ValueError(f"Finding has no evidence citations: {finding.title}")
        for finding in decision.findings:
            if not set(finding.evidence_ids).issubset(allowed_ids):
                raise ValueError(f"Finding cites unknown evidence: {finding.title}")
        if not self._config.redact_sensitive_values:
            return decision
        return Decision(
            summary=self._redact(decision.summary),
            findings=tuple(
                replace(
                    finding,
                    title=self._redact(finding.title),
                    explanation=self._redact(finding.explanation),
                )
                for finding in decision.findings
            ),
            open_questions=tuple(self._redact(question) for question in decision.open_questions),
        )

    def validate_output(self, report_text: str) -> str:
        return self._redact(report_text) if self._config.redact_sensitive_values else report_text

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _redact(value: str) -> str:
        for pattern in _SENSITIVE_PATTERNS:
            value = pattern.sub("[REDACTED]", value)
        return value
