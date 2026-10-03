"""Stage config."""

from typing import Any

from pydantic import Field

from trace_impact.shared.contracts import Contract
from trace_impact.shared.names import ComponentName


class StageConfig(Contract):
    provider: ComponentName
    options: dict[str, Any] = Field(default_factory=dict)
