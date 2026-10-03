"""Fetch bytes only. Loaders do not parse, chunk, extract, or write to databases."""

import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

import httpx

from trace_impact.ingestion.config import Project, Source
from trace_impact.ingestion.models import RawDocument

MAX_BYTES = 5_000_000


class LocalFileLoader:
    version = "local-file-v1"

    def load(self, source: Source, project: Project, config_dir: Path):
        path = (config_dir / source.location).resolve()
        if not path.is_relative_to(config_dir.resolve()):
            raise ValueError("Local source must stay inside the project configuration directory")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Source exceeds 5 MB")
        yield RawDocument(content=path.read_bytes(), location=source.location, key=source.location)


class WebLoader:
    version = "public-https-v1"

    def __init__(self, client: httpx.Client | None = None):
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=30, follow_redirects=False, headers={"User-Agent": "TraceImpact/0.3 ingestion"}
        )

    def close(self):
        if self._owns_client:
            self.client.close()

    def load(self, source: Source, project: Project, config_dir: Path):
        content, location = self.read(source, project, config_dir)
        yield RawDocument(content=content, location=location, key=source.location)

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


class GitHubFileLoader:
    version = "github-public-file-v1"

    def __init__(self, web: WebLoader | None = None):
        self.web = web or WebLoader()

    def close(self):
        self.web.close()

    def load(self, source: Source, project: Project, config_dir: Path):
        repository = source.options.get("repository", "")
        revision = source.options.get("revision") or source.version
        path = source.options.get("path", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("GitHub loader requires an owner/repository")
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or revision != source.version:
            raise ValueError("GitHub loader requires a pinned commit matching source.version")
        if not path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or "\\" in path:
            raise ValueError("GitHub path must be a relative repository file")
        url = f"https://raw.githubusercontent.com/{repository}/{revision}/{quote(path, safe='/')}"
        remote = source.model_copy(update={"location": url})
        for raw in self.web.load(remote, project, config_dir):
            yield raw.model_copy(update={"key": path})


# Preserve the earlier reader helper for downstream callers during migration.
HttpSourceReader = WebLoader


def read_source(source, project, config_dir):
    if source.location.startswith(("http://", "https://")):
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            return WebLoader(client).read(source, project, config_dir)
    raw = next(iter(LocalFileLoader().load(source, project, config_dir)))
    return raw.content, raw.location
