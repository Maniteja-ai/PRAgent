"""Compatibility exports for deterministic requirement rules."""

from .domain.policies import deduplicate, normalized, validate_candidate

__all__ = ["deduplicate", "normalized", "validate_candidate"]
