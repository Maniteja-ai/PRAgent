"""Shared strict settings behavior for configuration validators."""

from pydantic import BaseModel, ConfigDict


class StrictSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
