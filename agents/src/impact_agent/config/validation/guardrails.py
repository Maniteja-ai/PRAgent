"""Validation for ``guardrails.json``."""

from pydantic import Field

from impact_agent.config.validation.common import StrictSettings


class GuardrailConfig(StrictSettings):
    require_evidence_for_findings: bool = True
    block_untrusted_instructions: bool = True
    redact_sensitive_values: bool = True
    max_evidence_characters: int = Field(default=200_000, ge=1_000, le=2_000_000)
