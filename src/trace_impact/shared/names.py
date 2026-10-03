"""Canonical names for registered components; never applied to paths or model IDs."""

import re
from typing import Annotated

from pydantic import BeforeValidator, Field


def normalize_component_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Component name must be a string")
    name = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        raise ValueError("Component name must contain letters, digits, underscores or hyphens")
    return name


ComponentName = Annotated[
    str,
    BeforeValidator(normalize_component_name),
    Field(pattern=r"^\s*[A-Za-z0-9_-]+\s*$"),
]
