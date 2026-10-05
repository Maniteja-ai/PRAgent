"""Load six focused JSON files into one validated application configuration."""

import json
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv

from ingestion.config_loader.interface import ConfigLoader
from ingestion.config_loader.models import (
    ApplicationConfig,
    ApplicationManifest,
    ChunkingStrategyConfig,
    ConfigBean,
    ConstraintsConfig,
    EvaluationConfig,
    InputConfig,
    ModelsConfig,
    StorageConfig,
)

ConfigBeanT = TypeVar("ConfigBeanT", bound=ConfigBean)


class JsonConfigLoader(ConfigLoader):
    def load(self, path: Path) -> ApplicationConfig:
        manifest_path = path.resolve(strict=True)
        manifest = self._read(manifest_path, ApplicationManifest)
        if manifest.env_file:
            load_dotenv((manifest_path.parent / manifest.env_file).resolve(), override=False)

        section_paths = {
            name: (manifest_path.parent / section_path).resolve(strict=True)
            for name, section_path in manifest.files
        }
        input_config = self._resolve_input_paths(
            self._read(section_paths["input"], InputConfig), section_paths["input"].parent
        )
        storage = self._resolve_storage_paths(
            self._read(section_paths["storage"], StorageConfig), section_paths["storage"].parent
        )
        evaluation = self._resolve_evaluation_paths(
            self._read(section_paths["evaluation"], EvaluationConfig),
            section_paths["evaluation"].parent,
        )
        return ApplicationConfig(
            schema_version=manifest.schema_version,
            env_file=manifest.env_file,
            project=manifest.project,
            input=input_config,
            models=self._read(section_paths["models"], ModelsConfig),
            chunking=self._read(section_paths["chunking"], ChunkingStrategyConfig),
            storage=storage,
            constraints=self._read(section_paths["constraints"], ConstraintsConfig),
            evaluation=evaluation,
        )

    @staticmethod
    def _read(path: Path, model: type[ConfigBeanT]) -> ConfigBeanT:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: line {exc.lineno}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Configuration file must contain a JSON object: {path}")
        raw.pop("$schema", None)
        return model.model_validate(raw)

    @staticmethod
    def _resolve_input_paths(config: InputConfig, base: Path) -> InputConfig:
        code = config.code
        if code is None or code.repository_path.is_absolute():
            return config
        return config.model_copy(
            update={
                "code": code.model_copy(
                    update={"repository_path": (base / code.repository_path).resolve()}
                )
            }
        )

    @staticmethod
    def _resolve_storage_paths(config: StorageConfig, base: Path) -> StorageConfig:
        artifacts = config.artifacts
        if not artifacts.run_directory.is_absolute():
            artifacts = artifacts.model_copy(
                update={"run_directory": (base / artifacts.run_directory).resolve()}
            )
        vector = config.vector
        connection = vector.connection
        if connection is not None and connection.path is not None and not connection.path.is_absolute():
            connection = connection.model_copy(update={"path": (base / connection.path).resolve()})
            vector = vector.model_copy(update={"connection": connection})
        return config.model_copy(update={"artifacts": artifacts, "vector": vector})

    @staticmethod
    def _resolve_evaluation_paths(config: EvaluationConfig, base: Path) -> EvaluationConfig:
        recording = config.recording
        directory = recording.directory
        resolved_recording = recording
        if directory is not None and not directory.is_absolute():
            resolved_recording = recording.model_copy(update={"directory": (base / directory).resolve()})
        dataset = config.dataset_manifest
        if dataset is not None and not dataset.is_absolute():
            dataset = (base / dataset).resolve(strict=True)
        return config.model_copy(
            update={
                "recording": resolved_recording,
                "dataset_manifest": dataset,
            }
        )
