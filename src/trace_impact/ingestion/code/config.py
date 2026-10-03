from pathlib import Path
from typing import Literal

from pydantic import Field

from trace_impact.shared.contracts import Contract
from trace_impact.shared.stage_config import StageConfig


class TypeScriptOptions(Contract):
    tsconfig: str = "tsconfig.json"
    source_prefixes: tuple[str, ...] = ("src/",)
    max_files: int = Field(default=2000, ge=1, le=10000)
    max_bytes: int = Field(default=30000000, ge=1000, le=100000000)
    timeout_seconds: int = Field(default=90, ge=1, le=300)


class CodeGraphConfig(Contract):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1)
    repository_path: str = Field(
        min_length=1, description="Local Git checkout; resolved relative to this JSON."
    )
    revision: str = Field(
        pattern=r"^[a-f0-9]{40}$", description="Full pinned commit SHA; dirty worktree changes are excluded."
    )
    analyzer: StageConfig = Field(default_factory=lambda: StageConfig(provider="typescript"))


def load_code_config(path):
    path = Path(path)
    config = CodeGraphConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    return config.model_copy(
        update={"repository_path": str((path.resolve().parent / config.repository_path).resolve())}
    )


def code_schema(components):
    from trace_impact.shared.schema import add_stage_schemas

    schema = CodeGraphConfig.model_json_schema()
    add_stage_schemas(schema, (("analyzer", components.code_analyzers),))
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Pinned code graph ingestion"
    return schema
