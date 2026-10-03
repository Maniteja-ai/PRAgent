"""Load a composed ingestion configuration without changing process state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from trace_impact.ingestion.configuration.models import (
    ConfigurationPaths,
    EvaluationConfig,
    IngestionConfiguration,
    IngestionEntry,
    InputConfig,
    ProcessingConfig,
    StorageConfig,
)
from trace_impact.ingestion.configuration.models import RuntimeConfig as IngestionRuntimeConfig

ConfigT = TypeVar("ConfigT", bound=BaseModel)


def _read(path: Path, model: type[ConfigT]) -> ConfigT:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"Referenced configuration file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc.msg} at line {exc.lineno}") from exc
    return model.model_validate(value)


def _owned_path(owner: Path, value: str) -> Path:
    return (owner.parent / value).resolve()


def load_ingestion_configuration(path: Path) -> IngestionConfiguration:
    entry_path = path.resolve()
    entry = _read(entry_path, IngestionEntry)
    paths = ConfigurationPaths(
        entry=entry_path,
        inputs=_owned_path(entry_path, entry.config.inputs),
        processing=_owned_path(entry_path, entry.config.processing),
        storage=_owned_path(entry_path, entry.config.storage),
        runtime=_owned_path(entry_path, entry.config.runtime),
        evaluation=_owned_path(entry_path, entry.config.evaluation),
    )
    inputs = _read(paths.inputs, InputConfig)
    storage = _read(paths.storage, StorageConfig)
    evaluation = _read(paths.evaluation, EvaluationConfig)
    resolved_storage = storage.model_copy(
        update={
            "artifacts": storage.artifacts.model_copy(
                update={"run_directory": str(_owned_path(paths.storage, storage.artifacts.run_directory))}
            ),
            "candidates": storage.candidates.model_copy(
                update={"directory": str(_owned_path(paths.storage, storage.candidates.directory))}
            ),
        }
    )
    resolved_evaluation = evaluation.model_copy(
        update={
            "recording": evaluation.recording.model_copy(
                update={"directory": str(_owned_path(paths.evaluation, evaluation.recording.directory))}
            ),
            "golden_dataset": (
                str(_owned_path(paths.evaluation, evaluation.golden_dataset))
                if evaluation.golden_dataset
                else None
            ),
        }
    )
    code = inputs.code
    if code is not None:
        inputs = inputs.model_copy(
            update={
                "code": code.model_copy(
                    update={"repository_path": str(_owned_path(paths.inputs, code.repository_path))}
                )
            }
        )
    return IngestionConfiguration(
        entry=entry,
        inputs=inputs,
        processing=_read(paths.processing, ProcessingConfig),
        storage=resolved_storage,
        runtime=_read(paths.runtime, IngestionRuntimeConfig),
        evaluation=resolved_evaluation,
        paths=paths,
    )
