"""JSON contracts for a pipeline-independent retrieval evaluator."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReferenceFields(StrictModel):
    case_id: str = Field(default="id", min_length=1, description="Dotted field path containing the case ID.")
    inputs: str = Field(
        default="input", min_length=1, description="Input-only object passed to a Python adapter."
    )
    expected_ids: str | None = Field(default="reference.expected_ids", description="Expected relevant IDs.")
    relevance_grades: str | None = Field(
        default=None, description="Alternative: object mapping candidate IDs to grades."
    )
    relevant_grade: int = Field(default=2, ge=1, description="Minimum grade considered relevant.")
    candidate_universe: str | None = Field(
        default=None, description="Optional exhaustive list of judged candidate IDs."
    )
    evidence_units: str | None = Field(default=None, description="Optional list of required evidence units.")
    unit_alternatives: str = Field(default="acceptable_passage_ids", min_length=1)
    expected_status: str | None = Field(
        default=None, description="Optional expected application status, such as UNMAPPED."
    )
    approval: str = Field(
        default="review.approved", min_length=1, description="True only after independent review."
    )
    complete: bool = Field(
        default=False, description="Declare whether reference labels are exhaustive within the case scope."
    )

    @model_validator(mode="after")
    def one_reference_format(self):
        if (self.expected_ids is None) == (self.relevance_grades is None):
            raise ValueError("Select exactly one of expected_ids or relevance_grades")
        for value in (
            self.expected_ids,
            self.relevance_grades,
            self.candidate_universe,
            self.evidence_units,
            self.expected_status,
        ):
            if value == "":
                raise ValueError("Field paths must be nonempty or null")
        return self


class PredictionFields(StrictModel):
    case_id: str = Field(default="id", min_length=1)
    items: str = Field(
        default="retrieved_ids",
        min_length=1,
        description="Dotted path to the result list; rank order is preserved.",
    )
    item_id: str | None = Field(
        default=None, description="For object results, the dotted ID path inside each item."
    )
    execution_status: str = Field(
        default="execution_status", min_length=1, description="OK, ERROR or MISSING; absent means OK."
    )
    result_status: str = Field(
        default="status",
        min_length=1,
        description="Optional application status, distinct from execution success.",
    )


class Gates(StrictModel):
    precision_min: float | None = Field(default=None, ge=0, le=1)
    recall_min: float | None = Field(default=None, ge=0, le=1)


class EvaluationConfig(StrictModel):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    dataset: Path = Field(description="JSONL reference cases; relative paths resolve beside this config.")
    predictions: Path | None = Field(
        default=None, description="JSONL saved outputs; omit only when supplying a Python adapter."
    )
    output_directory: Path = Field(default="evaluation-results", validate_default=True)
    task: Literal["ranked_retrieval", "set_retrieval"] = "ranked_retrieval"
    k: int = Field(
        default=5,
        ge=1,
        le=1000,
        description="Top-k cutoff for ranked retrieval; set retrieval uses every returned ID.",
    )
    mode: Literal["development", "release"] = "development"
    reference: ReferenceFields = Field(default_factory=ReferenceFields)
    prediction: PredictionFields = Field(default_factory=PredictionFields)
    gates: Gates = Field(default_factory=Gates)


class Prediction(StrictModel):
    retrieved_ids: list[str] = Field(default_factory=list)
    execution_status: Literal["OK", "ERROR", "MISSING"] = "OK"
    result_status: str | None = None

    @model_validator(mode="after")
    def valid_output(self):
        if any(not id for id in self.retrieved_ids):
            raise ValueError("Result IDs must be nonempty strings")
        if self.execution_status != "OK" and self.retrieved_ids:
            raise ValueError("Failed or missing predictions must not contain success results")
        return self
