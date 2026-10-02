"""Source strategies selected explicitly by URI scheme; no application-specific logic."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..application.ports import SourceReader
from ..domain.errors import SourceReadError
from ..domain.models import Project, Snapshot, Source, stable_id
from .parsing import NORMALIZER_VERSION, make_chunks, normalize

MAX_BYTES = 5_000_000


class LocalFileReader:
    def read(self, source: Source, project: Project, config_dir: Path) -> tuple[bytes, str]:
        path = (config_dir / source.location).resolve()
        if not path.is_relative_to(config_dir.resolve()):
            raise ValueError("Local source must stay inside the project configuration directory")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Source exceeds 5 MB")
        return path.read_bytes(), source.location


class HttpSourceReader:
    def __init__(self, client: httpx.Client):
        self.client = client

    def read(self, source: Source, project: Project, config_dir: Path) -> tuple[bytes, str]:
        url = source.location
        for _ in range(6):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in project.allowed_document_hosts:
                raise ValueError("Document URL or redirect is outside the configured HTTPS hosts")
            if parsed.username or parsed.password:
                raise ValueError("Credentials in document URLs are not supported")
            with self.client.stream("GET", url, follow_redirects=False) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                data = bytearray()
                for block in response.iter_bytes():
                    data.extend(block)
                    if len(data) > MAX_BYTES:
                        raise ValueError("Source exceeds 5 MB")
                return bytes(data), str(response.url)
        raise ValueError("Too many redirects")


class SourceReaderRegistry:
    def __init__(self, readers: dict[str, SourceReader]):
        self.readers = dict(readers)

    def read(self, source: Source, project: Project, config_dir: Path) -> tuple[bytes, str]:
        scheme = urlparse(source.location).scheme
        if scheme not in self.readers:
            raise ValueError("Unsupported source scheme; register an explicit reader")
        return self.readers[scheme].read(source, project, config_dir)


class SnapshotDocumentProcessor:
    def __init__(self, reader: SourceReader):
        self.reader = reader

    def process(self, source: Source, project: Project, config_dir: Path, run_dir: Path):
        try:
            raw, resolved = self.reader.read(source, project, config_dir)
            text = normalize(raw, source.format)
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise SourceReadError("Source could not be read or normalized; verify its configuration") from exc
        raw_hash = hashlib.sha256(raw).hexdigest()
        normalized_hash = hashlib.sha256(text.encode()).hexdigest()
        # Authority, scope and source location are evidence semantics, not mutable metadata.
        sid = stable_id(
            project.project_id, source.model_dump_json(), NORMALIZER_VERSION, raw_hash, normalized_hash
        )
        folder = run_dir / "sources" / source.id
        folder.mkdir(parents=True)
        raw_path = folder / ("raw.html" if source.format == "html" else "raw.md")
        text_path = folder / "normalized.md"
        raw_path.write_bytes(raw)
        text_path.write_text(text, encoding="utf-8")
        snapshot = Snapshot(
            id=sid,
            source_id=source.id,
            location=source.location,
            resolved_location=resolved,
            version=source.version,
            authority=source.authority,
            scope=source.scope,
            retrieved_at=datetime.now(UTC).isoformat(),
            raw_sha256=raw_hash,
            normalized_sha256=normalized_hash,
            raw_file=raw_path.relative_to(run_dir).as_posix(),
            text_file=text_path.relative_to(run_dir).as_posix(),
        )
        return snapshot, make_chunks(text, sid, source.id, project.max_chunk_chars)
