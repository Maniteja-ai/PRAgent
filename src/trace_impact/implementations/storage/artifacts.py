"""Local artifact repository. Atomic replacement plus OS-backed run locking."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import BaseModel, ValidationError

from trace_impact.errors import ArtifactError, RunBusyError
from trace_impact.interfaces import ModelT


class FileArtifactRepository:
    def read(self, path: Path, model: type[ModelT]) -> ModelT:
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise ArtifactError("Artifact is missing, unreadable, or violates its schema") from exc

    def write(self, path: Path, value: BaseModel | dict) -> None:
        data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        self.write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as file:
                temp = Path(file.name)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def exists(self, path: Path) -> bool:
        return path.is_file()

    @contextmanager
    def lock(self, run_dir: Path):
        lock = FileLock(run_dir / ".ingestion.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout as exc:
            raise RunBusyError("Another process is using this run; retry after it finishes") from exc
        try:
            yield
        finally:
            lock.release()
