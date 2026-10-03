"""Deterministic safety checks around every decision-model call."""

import hashlib
import json
import re
from collections import Counter
from typing import Literal

from pydantic import Field

from trace_coordinator.domain.errors import FailureCode, ToolFailure
from trace_coordinator.domain.models import Decision, Record


class GuardrailPolicy(Record):
    enabled: bool = True
    request_sensitive_action: Literal["block", "allow"] = "block"
    evidence_sensitive_action: Literal["redact", "block"] = "redact"
    prompt_injection_action: Literal["quarantine", "block"] = "quarantine"
    output_sensitive_action: Literal["block"] = "block"
    max_findings_per_call: int = Field(default=100, ge=1, le=1000)


_PATTERNS = {
    "email": re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I),
    "phone": re.compile(
        r"(?<!\w)(?:\+\d[\d ()-]{7,}\d|\(\d{2,4}\)\s*\d{3,4}[ -]\d{3,4}|\d{3}[ -]\d{3}[ -]\d{4})(?!\w)"
    ),
    "ssn": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "api_key": re.compile(
        r"(?<![A-Za-z0-9])(?:AIza[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})(?![A-Za-z0-9])"
    ),
    "bearer_token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
}
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_INJECTION = re.compile(
    r"(?i)(ignore\s+(?:all\s+)?(?:previous|prior|system)|reveal\s+(?:the\s+)?(?:system|developer)\s+prompt|"
    r"follow\s+these\s+new\s+instructions|override\s+(?:the\s+)?(?:policy|guardrails?|instructions)|"
    r"exfiltrat(?:e|ion)|disable\s+(?:the\s+)?guardrails?)"
)


def _luhn(value):
    digits = [int(character) for character in re.sub(r"\D", "", value)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def findings(text):
    detected = Counter()
    for name, pattern in _PATTERNS.items():
        detected[name] += len(pattern.findall(text))
    detected["payment_card"] += sum(1 for match in _CARD_CANDIDATE.findall(text) if _luhn(match))
    detected["prompt_injection"] += len(_INJECTION.findall(text))
    return {name: count for name, count in sorted(detected.items()) if count}


def _redact_sensitive(text, counts):
    for name, pattern in _PATTERNS.items():
        text, count = pattern.subn(f"[REDACTED_{name.upper()}]", text)
        if count:
            counts[name] += count

    def card(match):
        if _luhn(match.group(0)):
            counts["payment_card"] += 1
            return "[REDACTED_PAYMENT_CARD]"
        return match.group(0)

    return _CARD_CANDIDATE.sub(card, text)


class GuardrailEngine:
    def __init__(self, policy=None):
        self.policy = GuardrailPolicy.model_validate(policy or {})
        canonical = json.dumps(
            self.policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        self.version = "guardrails-v1:" + hashlib.sha256(canonical).hexdigest()

    def validate_user_text(self, text, *, field):
        if not self.policy.enabled:
            return
        detected = findings(text)
        blocked = {
            name: count
            for name, count in detected.items()
            if name == "prompt_injection" or self.policy.request_sensitive_action == "block"
        }
        if blocked:
            categories = ", ".join(blocked)
            raise ToolFailure(f"Guardrail blocked {field}: {categories}", code=FailureCode.GUARDRAIL_BLOCKED)

    def prepare_model_input(self, payload):
        if not self.policy.enabled:
            return payload, {"status": "DISABLED", "counts": {}}
        self.validate_user_text(payload.get("request", {}).get("question", ""), field="request")
        for answer in payload.get("human_answers", []):
            self.validate_user_text(answer, field="human review")
        counts = Counter()

        def clean(value):
            if isinstance(value, str):
                if _INJECTION.search(value):
                    counts["prompt_injection"] += len(_INJECTION.findall(value))
                    if self.policy.prompt_injection_action == "block":
                        raise ToolFailure(
                            "Guardrail blocked untrusted prompt injection",
                            code=FailureCode.GUARDRAIL_BLOCKED,
                        )
                    return "[UNTRUSTED_CONTENT_QUARANTINED]"
                detected = {
                    name: count for name, count in findings(value).items() if name != "prompt_injection"
                }
                if detected and self.policy.evidence_sensitive_action == "block":
                    raise ToolFailure(
                        "Guardrail blocked sensitive evidence: " + ", ".join(detected),
                        code=FailureCode.GUARDRAIL_BLOCKED,
                    )
                return _redact_sensitive(value, counts)
            if isinstance(value, dict):
                return {key: clean(child) for key, child in value.items()}
            if isinstance(value, list):
                return [clean(child) for child in value]
            if isinstance(value, tuple):
                return tuple(clean(child) for child in value)
            return value

        safe = dict(payload)
        safe["evidence"] = clean(payload.get("evidence", {}))
        if sum(counts.values()) > self.policy.max_findings_per_call:
            raise ToolFailure("Guardrail finding limit exceeded", code=FailureCode.GUARDRAIL_BLOCKED)
        audit = {
            "status": "SANITIZED" if counts else "PASSED",
            "counts": dict(sorted(counts.items())),
        }
        return safe, audit

    def validate_model_output(self, decision):
        parsed = Decision.model_validate(decision)
        if not self.policy.enabled:
            return parsed, {"status": "DISABLED", "counts": {}}
        output = parsed.model_dump_json()
        detected = {name: count for name, count in findings(output).items() if name != "prompt_injection"}
        if detected:
            raise ToolFailure(
                "Guardrail blocked sensitive model output: " + ", ".join(detected),
                code=FailureCode.GUARDRAIL_BLOCKED,
            )
        return parsed, {"status": "PASSED", "counts": {}}
