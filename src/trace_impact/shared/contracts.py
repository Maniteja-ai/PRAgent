"""Contracts."""

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
