"""Project-defined metadata fields for JSON configuration and ingestion."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MetadataField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description="JSON key for this custom field.")
    title: str = Field(min_length=1, description="Readable label for this metadata field.")
    description: str = Field(default="", description="Help text explaining the field.")
    type: Literal["string", "number", "boolean", "string_list"] = Field(
        default="string", description="Value type. String choices supply JSON editor suggestions."
    )
    required: bool = Field(
        default=False, description="Every source must supply this field or inherit a default."
    )
    choices: list[str] = Field(
        default_factory=list, description="Optional allowed values for a string field."
    )

    @model_validator(mode="after")
    def check_choices(self):
        if self.choices and self.type != "string":
            raise ValueError("Dropdown choices are supported for string fields only")
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("Dropdown choices must be unique")
        if self.name in {
            "constructor",
            "prototype",
            "run_id",
            "chunk_id",
            "source_id",
            "snapshot_id",
            "raw_sha256",
            "normalized_sha256",
        }:
            raise ValueError("System provenance names cannot be used as custom metadata fields")
        return self

    def value_schema(self) -> dict:
        result = {"title": self.title, "description": self.description}
        result["type"] = "array" if self.type == "string_list" else self.type
        if self.type == "string_list":
            result["items"] = {"type": "string"}
        if self.choices:
            result["enum"] = self.choices
        return result


class MetadataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[MetadataField] = Field(
        default_factory=list, description="Definitions for custom source metadata."
    )
    defaults: dict[str, Any] = Field(
        default_factory=dict, description="Shared values inherited by every source."
    )

    @model_validator(mode="after")
    def definitions_valid(self):
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("Metadata field names must be unique")
        self.validate_values(self.defaults, require_all=False)
        return self

    def validate_values(self, values: dict, *, require_all=True) -> dict:
        import math

        definitions = {field.name: field for field in self.fields}
        if set(values) - definitions.keys():
            raise ValueError("Metadata contains fields without a definition")
        for name, field in definitions.items():
            if name not in values:
                if require_all and field.required:
                    raise ValueError(f"Required metadata field '{name}' is missing")
                continue
            value = values[name]
            valid = {
                "string": isinstance(value, str),
                "number": type(value) in (int, float) and math.isfinite(value),
                "boolean": type(value) is bool,
                "string_list": isinstance(value, list) and all(isinstance(v, str) for v in value),
            }[field.type]
            if not valid or (field.choices and value not in field.choices):
                raise ValueError(f"Invalid value for metadata field '{name}'")
            if field.required and isinstance(value, str) and not value.strip():
                raise ValueError(f"Required metadata field '{name}' must not be blank")
        return values

    def for_source(self, overrides: dict) -> dict:
        return self.validate_values({**self.defaults, **overrides})
