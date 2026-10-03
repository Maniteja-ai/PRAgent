"""Apply one scanner policy to all artifacts written under a state root."""

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from trace_coordinator.domain.contracts import JsonObject, as_json_object
from trace_coordinator.security.artifact_security.config import ArtifactSecurityConfig
from trace_coordinator.security.artifact_security.factory import build_scanner
from trace_coordinator.security.artifact_security.interface import ArtifactScanner

_LOCK = threading.RLock()
_POLICIES: dict[Path, tuple[ArtifactSecurityConfig, ArtifactScanner]] = {}


@contextmanager
def artifact_security(
    root: str | Path,
    config: ArtifactSecurityConfig,
    *,
    scanner: ArtifactScanner | None = None,
) -> Iterator[None]:
    selected_root = Path(root).resolve()
    selected_scanner = scanner if scanner is not None else build_scanner(config)
    if selected_scanner is None:
        yield
        return
    with _LOCK:
        if selected_root in _POLICIES:
            raise RuntimeError("Artifact security is already configured for this root")
        _POLICIES[selected_root] = (config, selected_scanner)
    try:
        yield
    finally:
        with _LOCK:
            _POLICIES.pop(selected_root, None)


def _audit(root: Path, event: JsonObject) -> None:
    directory = root / "security"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "artifact-dlp.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def enforce_artifact_policy(root: str | Path, data: bytes, suffix: str) -> None:
    selected_root = Path(root).resolve()
    with _LOCK:
        selected = _POLICIES.get(selected_root)
    if selected is None:
        return
    config, scanner = selected
    event = as_json_object(
        {
            "at": time.time(),
            "bytes": len(data),
            "suffix": suffix,
            "sha256": hashlib.sha256(data).hexdigest(),
            "scanner": scanner.version,
        }
    )
    if len(data) > config.max_bytes:
        event.update(status="BLOCKED", reason="size_limit")
        _audit(selected_root, event)
        raise ValueError("Artifact blocked by DLP size policy")
    try:
        result = scanner.inspect(data, suffix, config)
    except Exception as exc:
        event.update(status="BLOCKED", reason="scanner_error", error=type(exc).__name__)
        _audit(selected_root, event)
        if config.scanner_error_action == "block":
            raise ValueError("Artifact blocked because DLP scanning failed") from exc
        return
    event.update(status=result.status, findings=as_json_object(result.findings))
    _audit(selected_root, event)
    if result.status == "SENSITIVE":
        raise ValueError("Artifact blocked by DLP policy: " + ", ".join(result.findings))
    if result.status == "UNSUPPORTED" and config.unsupported_action == "block":
        raise ValueError("Artifact blocked because its content type is unsupported by DLP")
