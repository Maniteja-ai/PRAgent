"""Explicit HTTP/local adapters and deterministic document normalization."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from markdownify import markdownify

from .models import Chunk, Project, Snapshot, Source, stable_id

MAX_BYTES = 5_000_000
NORMALIZER_VERSION = "main-markdown-v1"


def read_source(source: Source, project: Project, config_dir: Path) -> tuple[bytes, str]:
    if not source.location.startswith(("http://", "https://")):
        path = (config_dir / source.location).resolve()
        if not path.is_relative_to(config_dir.resolve()):
            raise ValueError("Local source must stay inside the project configuration directory")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Source exceeds 5 MB")
        return path.read_bytes(), source.location
    url = source.location
    with httpx.Client(
        timeout=30, follow_redirects=False, headers={"User-Agent": "TraceImpact/0.1 documentation-ingestion"}
    ) as client:
        for _ in range(6):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in project.allowed_document_hosts:
                raise ValueError("Document URL or redirect is outside the configured HTTPS hosts")
            if parsed.username or parsed.password:
                raise ValueError("Credentials in document URLs are not supported")
            with client.stream("GET", url) as response:
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


def normalize(raw: bytes, fmt: str) -> str:
    text = raw.decode("utf-8-sig")
    if fmt == "html":
        soup = BeautifulSoup(text, "html.parser")
        root = soup.find("main") or soup.find("article")
        if root is None:
            raise ValueError("No main/article found; supply Markdown or a source-specific adapter")
        for node in root.select("script, style, nav, header, footer, button, svg"):
            node.decompose()
        text = markdownify(str(root), heading_style="ATX")
    text = text.replace("\r\n", "\n").strip()
    if len(text) < 40:
        raise ValueError("Document has too little usable text")
    return text


def make_chunks(text: str, snapshot_id: str, source_id: str, limit: int) -> list[Chunk]:
    # Markdown token maps prevent '# headings' inside fenced examples from splitting sections.
    tokens = MarkdownIt().parse(text)
    lines = text.splitlines(keepends=True)
    sections: list[tuple[str, str]] = []
    heading, start = "Document", 0
    stack: list[tuple[int, str]] = []
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        pos = token.map[0]
        if pos > start:
            sections.append((heading, "".join(lines[start:pos]).strip()))
        depth = int(token.tag[1:])
        stack = [(d, title) for d, title in stack if d < depth]
        stack.append((depth, tokens[i + 1].content))
        heading = " > ".join(title for _, title in stack)
        start = pos
    sections.append((heading, "".join(lines[start:]).strip()))
    result: list[Chunk] = []
    for heading, section in sections:
        if not section:
            continue
        # Group top-level Markdown blocks; tables and code examples stay intact.
        block_tokens = MarkdownIt().parse(section)
        section_lines = section.splitlines(keepends=True)
        spans = []
        end = 0
        for token in block_tokens:
            if token.level == 0 and token.map and token.map[0] >= end:
                a, b = token.map
                spans.append("".join(section_lines[a:b]).strip())
                end = b
        current = ""
        for block in spans or [section]:
            if current and len(current) + len(block) + 2 > limit:
                ordinal = len(result)
                result.append(
                    Chunk(
                        id=stable_id(snapshot_id, str(ordinal), current),
                        snapshot_id=snapshot_id,
                        source_id=source_id,
                        heading=heading,
                        ordinal=ordinal,
                        text=current,
                        oversized=len(current) > limit,
                    )
                )
                current = ""
            current = f"{current}\n\n{block}".strip()
        if current:
            ordinal = len(result)
            result.append(
                Chunk(
                    id=stable_id(snapshot_id, str(ordinal), current),
                    snapshot_id=snapshot_id,
                    source_id=source_id,
                    heading=heading,
                    ordinal=ordinal,
                    text=current,
                    oversized=len(current) > limit,
                )
            )
    return result


def snapshot_source(source: Source, project: Project, config_dir: Path, run_dir: Path):
    raw, resolved = read_source(source, project, config_dir)
    text = normalize(raw, source.format)
    raw_hash = hashlib.sha256(raw).hexdigest()
    normalized_hash = hashlib.sha256(text.encode()).hexdigest()
    sid = stable_id(
        project.project_id, source.id, source.version, NORMALIZER_VERSION, raw_hash, normalized_hash
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
