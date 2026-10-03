"""Typed plugin configuration definitions for JSON completion and runtime checks."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmptyOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitHubOptions(EmptyOptions):
    repository: str = Field(
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
        title="Repository",
        description="Public GitHub owner/repository, for example saleor/storefront.",
    )
    revision: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{40}$",
        title="Pinned commit",
        description="Full commit SHA. If omitted, the source version is used.",
    )
    path: str = Field(
        min_length=1, title="File path", description="Repository-relative file path, for example README.md."
    )

    @field_validator("path")
    @classmethod
    def relative_path(cls, value):
        from pathlib import PurePosixPath

        if PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts or "\\" in value:
            raise ValueError("Use a relative repository file path")
        return value


@dataclass(frozen=True)
class ComponentDefinition:
    title: str
    description: str
    options_model: type[BaseModel] | None = None

    def schema(self):
        return {
            "title": self.title,
            "description": self.description,
            "options_schema": self.options_model.model_json_schema() if self.options_model else None,
        }
