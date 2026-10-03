"""Processor."""

import hashlib
import json
from datetime import UTC, datetime

import httpx

from trace_impact.ingestion.models import Snapshot, stable_id
from trace_impact.shared.errors import SourceReadError


class ConfiguredDocumentProcessor:
    def __init__(self, components, artifacts):
        self.components, self.artifacts = components, artifacts

    def process(self, source, project, config_dir, run_dir):
        loader = self.components.loaders.resolve(source.loader_name)
        parser = self.components.parsers.resolve(source.parser_name)
        chunker = self.components.chunkers.resolve(project.chunker)
        version = stable_id(loader.version, parser.version, chunker.version)
        documents, keys = [], set()
        try:
            for raw in loader.load(source, project, config_dir):
                if raw.key in keys:
                    raise ValueError("Loader returned duplicate document keys")
                keys.add(raw.key)
                document = parser.parse(raw)
                raw_hash = hashlib.sha256(raw.content).hexdigest()
                text_hash = hashlib.sha256(document.text.encode()).hexdigest()
                sid = stable_id(
                    project.project_id, source.model_dump_json(), raw.key, version, raw_hash, text_hash
                )
                metadata = project.metadata.for_source(source.metadata) if project.metadata else {}
                if metadata:
                    # Metadata affects provenance IDs, but does not change the embedded document text.
                    sid = stable_id(sid, json.dumps(metadata, sort_keys=True))
                chunks = chunker.split(document, sid, source.id, project.max_chunk_chars)
                if metadata:
                    chunks = [c.model_copy(update={"metadata": metadata.copy()}) for c in chunks]
                if not chunks or any(c.snapshot_id != sid or c.source_id != source.id for c in chunks):
                    raise ValueError(
                        "Chunker must return nonempty chunks with the supplied source/snapshot IDs"
                    )
                if len({c.id for c in chunks}) != len(chunks):
                    raise ValueError("Chunker returned duplicate chunk IDs")
                folder = run_dir / "sources" / source.id / sid
                raw_path, text_path = folder / "raw.bin", folder / "normalized.md"
                self.artifacts.write_bytes(raw_path, raw.content)
                self.artifacts.write_bytes(text_path, document.text.encode())
                snapshot = Snapshot(
                    id=sid,
                    source_id=source.id,
                    location=source.location or raw.location,
                    resolved_location=raw.location,
                    version=source.version,
                    authority=source.authority,
                    scope=source.scope,
                    retrieved_at=datetime.now(UTC).isoformat(),
                    raw_sha256=raw_hash,
                    normalized_sha256=text_hash,
                    raw_file=raw_path.relative_to(run_dir).as_posix(),
                    text_file=text_path.relative_to(run_dir).as_posix(),
                    document_key=raw.key,
                    processor_version=version,
                    metadata=metadata,
                )
                documents.append((snapshot, chunks))
            if not documents:
                raise ValueError("Loader returned no documents")
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise SourceReadError(
                "Source could not be loaded, parsed or chunked; check its configuration"
            ) from exc
        return documents
