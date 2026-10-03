"""Coordinator-owned boundary records; no dependency on the knowledge library."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisRequest(Record):
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1, max_length=100)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    pull_request: int = Field(gt=0)
    question: str = Field(default="Which UI flows and requirements could this PR affect?", max_length=4000)


class Evidence(Record):
    id: str = Field(min_length=1)
    project_id: str
    kind: Literal["diff", "graph", "document", "browser", "fixture", "verification", "attestation"]
    summary: str = Field(max_length=60000)
    source: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(Record):
    evidence: tuple[Evidence, ...] = Field(default=(), max_length=100)
    gaps: tuple[str, ...] = Field(default=(), max_length=100)


class Finding(Record):
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    checks: tuple[str, ...] = Field(default=(), max_length=20)
    # The foundation only predicts risk. Browser assertions require a later verifier.
    classification: Literal["POTENTIAL_IMPACT"] = "POTENTIAL_IMPACT"


class Decision(Record):
    action: Literal["tool", "review", "finish"]
    tool: str | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    question: str | None = Field(default=None, max_length=2000)
    findings: tuple[Finding, ...] = Field(default=(), max_length=30)

    @model_validator(mode="after")
    def coherent_action(self):
        if self.action == "tool":
            if not self.tool or self.question or self.findings:
                raise ValueError("Tool decisions require a tool and cannot contain findings or review")
        elif self.action == "review":
            if not self.question or self.tool or self.arguments or self.findings:
                raise ValueError("Review requires only a question")
        elif self.tool or self.arguments or self.question:
            raise ValueError("Finish cannot dispatch tools or request review")
        return self


class ReviewResponse(Record):
    answer: str = Field(min_length=1, max_length=4000)


class FileChange(Record):
    path: str
    status: Literal["A", "D", "M", "T"]


class ChangeSet(Record):
    repository: str
    pull_request: int
    upstream_base: str
    upstream_head: str
    comparison_base: str
    analysis_base: str
    analysis_head: str
    files: tuple[FileChange, ...]
    patch_sha256: str
    historical_replay: bool
    deployment_patch_equivalent: bool


class ToolContext(Record):
    run_id: str
    project_id: str
    agent_id: str
    changes: ChangeSet | None = None
    runtime_attestations: dict[str, JsonValue] = Field(default_factory=dict)
