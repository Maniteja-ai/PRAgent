"""Hashed evidence files with atomic writes; no mutable application state."""

import hashlib
import os
import tempfile
from pathlib import Path

from trace_coordinator.domain.contracts import ArtifactPayload


def save_artifact(root: Path, run_id: str, data: bytes, suffix: str) -> ArtifactPayload:
    from trace_coordinator.security.artifact_security import enforce_artifact_policy

    enforce_artifact_policy(root, data, suffix)
    digest = hashlib.sha256(data).hexdigest()
    directory = (root / run_id / "evidence").resolve()
    # Generated workspaces already have long names. Windows' legacy MAX_PATH
    # limit must not make success depend on the run ID or artifact extension.
    if os.name == "nt" and not str(directory).startswith("\\\\?\\"):
        value = str(directory)
        directory = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest + suffix)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Evidence hash collision")
    else:
        fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {"path": str(path), "sha256": digest, "bytes": len(data)}
