"""Small orchestration class; stage implementations remain replaceable adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from trace_impact.ingestion.code.config import CodeGraphConfig
from trace_impact.ingestion.configuration.legacy_adapter import (
    to_code_graph_config,
    to_document_project,
)
from trace_impact.ingestion.configuration.models import IngestionConfiguration
from trace_impact.ingestion.evaluation.recording import (
    JsonlStageRecorder,
    NullStageRecorder,
    StageRecorder,
    record_stage,
)
from trace_impact.ingestion.mappings import CoverageGap, JsonMappingCandidateStore, MappingBatch
from trace_impact.ingestion.models import Corpus, ExtractionRun, VectorIndexRun
from trace_impact.ingestion.orchestration.models import IngestionManifest, StageResult
from trace_impact.ingestion.storage.artifact_store import FileArtifactRepository
from trace_impact.shared.graph_models import GraphSnapshot

if TYPE_CHECKING:
    from trace_impact.pipeline import IngestionPipeline


class IngestionRunner:
    """Coordinate enabled stages and leave algorithms inside their focused services."""

    def __init__(self, pipeline: IngestionPipeline, config: IngestionConfiguration) -> None:
        self.pipeline = pipeline
        self.config = config
        self.run_id = uuid4().hex
        recording = config.evaluation.recording
        self.stage_recorder: StageRecorder = (
            JsonlStageRecorder(Path(recording.directory)) if recording.enabled else NullStageRecorder()
        )
        self._run_directory: Path | None = None
        self._corpus_run_id: str | None = None
        self._deadline = monotonic() + config.runtime.timeouts.run_seconds

    def execute(self) -> IngestionManifest:
        stages: list[StageResult] = []
        gaps: list[str] = []
        try:
            return self._execute(stages, gaps)
        except Exception as exc:
            stages.append(StageResult(name="pipeline", status="FAILED", detail=type(exc).__name__))
            if self._run_directory is not None:
                self._write_manifest(self._manifest(stages, gaps, status="FAILED"))
            raise

    def _execute(self, stages: list[StageResult], gaps: list[str]) -> IngestionManifest:
        self._validate()
        stages.append(StageResult(name="validate_configuration", status="COMPLETED"))

        run_directory, corpus = self._collect_documents()
        self._run_directory = run_directory
        self._corpus_run_id = corpus.run_id
        if len(corpus.snapshots) > self.config.runtime.limits.documents:
            raise ValueError("Collected document count exceeds runtime.limits.documents")
        if corpus.errors and self.config.runtime.failures.source_error == "stop":
            raise ValueError("Document collection failed and source_error policy is stop")
        stages.append(StageResult(name="collect_documents", status="COMPLETED", artifact=str(run_directory)))

        if self.config.processing.requirements.enabled:
            extraction = self._extract_requirements()
            stages.append(
                StageResult(
                    name="extract_requirements",
                    status="COMPLETED",
                    artifact=str(run_directory / "extraction.json"),
                    detail=f"{len(extraction.requirements)} requirements",
                )
            )
        else:
            stages.append(StageResult(name="extract_requirements", status="SKIPPED", detail="disabled"))

        if self.config.processing.embeddings.enabled and self.config.storage.vector.publish_chunks:
            index = self._index_vectors()
            stages.append(
                StageResult(
                    name="index_vectors",
                    status="COMPLETED",
                    artifact=str(run_directory / "vector-index.json"),
                    detail=f"{len(index.indexed_chunk_ids)} chunks",
                )
            )
        else:
            stages.append(StageResult(name="index_vectors", status="SKIPPED", detail="disabled"))

        code_snapshot = self._analyze_code_if_enabled(stages)
        self._mark_unavailable_knowledge_stages(stages, gaps)
        self._write_mapping_evidence(gaps)
        self._publish(stages, code_snapshot)
        manifest = self._manifest(stages, gaps)
        self._write_manifest(manifest)
        return manifest

    @record_stage("validate_configuration")
    def _validate(self) -> None:
        self._check_deadline()
        limits = self.config.runtime.limits
        if len(self.config.inputs.documents.sources) > limits.documents:
            raise ValueError("Configured document count exceeds runtime.limits.documents")
        if self.config.processing.ui.max_pages > limits.ui_pages:
            raise ValueError("UI max_pages exceeds runtime.limits.ui_pages")
        project = to_document_project(self.config)
        self.pipeline.validate_project(project)
        if self.config.inputs.code and self.config.inputs.code.enabled:
            self.pipeline.components.code_analyzers.require(self.config.processing.code.analyzer)
            self.pipeline.components.code_analyzers.validate_options(
                self.config.processing.code.analyzer.provider,
                self.config.processing.code.analyzer.options,
            )
        if self.config.inputs.ui and self.config.inputs.ui.enabled:
            raise ValueError("UI ingestion is enabled but no UI explorer adapter is registered")

    @record_stage("collect_documents")
    def _collect_documents(self) -> tuple[Path, Corpus]:
        self._check_deadline()
        project = to_document_project(self.config)
        return self.pipeline.collect_project(
            project,
            self.config.paths.inputs,
            Path(self.config.storage.artifacts.run_directory),
        )

    @record_stage("extract_requirements")
    def _extract_requirements(self) -> ExtractionRun:
        self._check_deadline()
        return self.pipeline.extract(
            self._require_run_directory(), self.config.processing.requirements.max_chunks
        )

    @record_stage("index_vectors")
    def _index_vectors(self) -> VectorIndexRun:
        self._check_deadline()
        return self.pipeline.index(
            self._require_run_directory(), self.config.processing.embeddings.batch_size
        )

    @record_stage("analyze_code")
    def _analyze_code(self, code_config: CodeGraphConfig) -> GraphSnapshot:
        self._check_deadline()
        return self.pipeline.analyze_code_config(code_config)

    @record_stage("publish_documents_and_requirements")
    def _publish_documents(self, include_requirements: bool) -> dict[str, int]:
        self._check_deadline()
        return self.pipeline.publish_graph(self._require_run_directory(), include_requirements)

    @record_stage("publish_code")
    def _publish_code(self, snapshot: GraphSnapshot) -> dict[str, int | str]:
        self._check_deadline()
        return self.pipeline.publish_code_graph(snapshot)

    def _analyze_code_if_enabled(self, stages: list[StageResult]) -> GraphSnapshot | None:
        code_config = to_code_graph_config(self.config)
        if code_config is None:
            stages.append(StageResult(name="analyze_code", status="SKIPPED", detail="disabled"))
            return None
        snapshot = self._analyze_code(code_config)
        stages.append(
            StageResult(
                name="analyze_code",
                status="COMPLETED",
                detail=f"{len(snapshot.nodes)} nodes, {len(snapshot.edges)} edges",
            )
        )
        return snapshot

    def _mark_unavailable_knowledge_stages(self, stages: list[StageResult], gaps: list[str]) -> None:
        if self.config.inputs.ui is None or not self.config.inputs.ui.enabled:
            stages.append(StageResult(name="crawl_ui", status="SKIPPED", detail="disabled"))
            stages.append(StageResult(name="validate_mappings", status="SKIPPED", detail="requires UI data"))
            gaps.append("UI evidence and general code-to-UI mappings were not ingested")

    def _write_mapping_evidence(self, gaps: list[str]) -> None:
        records = tuple(
            CoverageGap(
                id=f"{self.run_id}-gap-{index}",
                area="code-to-ui coverage",
                missing_relationship="RENDERS",
                reason=reason,
            )
            for index, reason in enumerate(gaps, start=1)
        )
        JsonMappingCandidateStore(Path(self.config.storage.candidates.directory)).write(
            MappingBatch(
                run_id=self.run_id,
                project_id=self.config.entry.project.id,
                coverage_gaps=records,
            )
        )

    def _publish(self, stages: list[StageResult], code_snapshot: GraphSnapshot | None) -> None:
        graph = self.config.storage.graph
        if graph.publish_documents:
            include_requirements = graph.publish_requirements and self.config.processing.requirements.enabled
            self._publish_documents(include_requirements)
            stages.append(StageResult(name="publish_documents_and_requirements", status="COMPLETED"))
        else:
            stages.append(
                StageResult(name="publish_documents_and_requirements", status="SKIPPED", detail="disabled")
            )
        if graph.publish_code and code_snapshot is not None:
            self._publish_code(code_snapshot)
            stages.append(StageResult(name="publish_code", status="COMPLETED"))
        else:
            stages.append(StageResult(name="publish_code", status="SKIPPED", detail="disabled"))

    def _manifest(
        self,
        stages: list[StageResult],
        gaps: list[str],
        *,
        status: Literal["COMPLETED", "COMPLETED_WITH_GAPS", "FAILED"] | None = None,
    ) -> IngestionManifest:
        config_hash = hashlib.sha256(self.config.model_dump_json(exclude={"paths"}).encode()).hexdigest()
        return IngestionManifest(
            run_id=self.run_id,
            corpus_run_id=self._corpus_run_id,
            project_id=self.config.entry.project.id,
            status=status or ("COMPLETED_WITH_GAPS" if gaps else "COMPLETED"),
            config_sha256=config_hash,
            stages=tuple(stages),
            coverage_gaps=tuple(gaps),
        )

    def _write_manifest(self, manifest: IngestionManifest) -> None:
        FileArtifactRepository().write(self._require_run_directory() / "ingestion-manifest.json", manifest)

    def _require_run_directory(self) -> Path:
        if self._run_directory is None:
            raise RuntimeError("Document collection must complete before this stage")
        return self._run_directory

    def _check_deadline(self) -> None:
        if monotonic() > self._deadline:
            raise TimeoutError("Ingestion run exceeded runtime.timeouts.run_seconds")
