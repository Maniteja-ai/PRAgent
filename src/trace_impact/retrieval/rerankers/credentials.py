"""Resolve an explicit key reference without putting its value in JSON or reports."""

import os

from pydantic import SecretStr

from trace_impact.shared.errors import ConfigurationError


def resolve_api_key(name: str | None, fallback: SecretStr | None = None) -> SecretStr:
    value = os.environ.get(name, "") if name else (fallback.get_secret_value() if fallback else "")
    if not value.strip():
        raise ConfigurationError(f"Set {name or 'GEMINI_API_KEY'} in local .env for reranking")
    return SecretStr(value.strip())
