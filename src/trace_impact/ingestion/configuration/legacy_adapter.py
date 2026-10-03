"""Translate the composed configuration at the boundary of existing adapters."""

from trace_impact.ingestion.code.config import CodeGraphConfig
from trace_impact.ingestion.config import Project
from trace_impact.ingestion.configuration.models import IngestionConfiguration


def to_document_project(config: IngestionConfiguration) -> Project:
    """Build the existing document contract without leaking split-file concerns into adapters."""
    return Project(
        project_id=config.entry.project.id,
        name=config.entry.project.name,
        repository=config.inputs.repository,
        baseline_url=config.inputs.baseline_url,
        scope=list(config.inputs.scope),
        allowed_document_hosts=list(config.inputs.documents.allowed_hosts),
        sources=list(config.inputs.documents.sources),
        max_chunk_chars=config.processing.documents.max_chunk_chars,
        excluded_inputs=list(config.inputs.excluded_inputs),
        chunker=config.processing.documents.chunker,
        extractor=config.processing.requirements.extractor,
        embedding_provider=config.processing.embeddings.provider,
        storage={
            "graph": config.storage.graph.provider,
            "vector": config.storage.vector.provider,
            "artifacts": config.storage.artifacts.provider,
        },
        metadata=config.inputs.documents.metadata,
    )


def to_code_graph_config(config: IngestionConfiguration) -> CodeGraphConfig | None:
    selected = config.inputs.code
    if selected is None or not selected.enabled:
        return None
    analyzer = config.processing.code.analyzer
    options = dict(analyzer.options)
    configured_limit = options.get("max_files", config.runtime.limits.code_files)
    if not isinstance(configured_limit, int):
        raise ValueError("Code analyzer max_files must be an integer")
    options["max_files"] = min(configured_limit, config.runtime.limits.code_files)
    return CodeGraphConfig(
        project_id=config.entry.project.id,
        repository_path=selected.repository_path,
        revision=selected.revision,
        analyzer=analyzer.model_copy(update={"options": options}),
    )
