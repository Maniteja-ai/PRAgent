"""The only adapter importing trace_impact. Existing library behavior is preserved."""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from trace_coordinator.application.mapping import MappingConfig
from trace_coordinator.domain.contracts import (
    AnalysisReportPayload,
    ArtifactPayload,
    JsonObject,
    ObservedElementPayload,
    SourceInspectionCandidatePayload,
    SourceProvenancePayload,
    ValidatedMappingPayload,
    as_json_object,
    as_json_value,
)
from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import Evidence, ToolContext, ToolResult
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.artifacts import save_artifact
from trace_coordinator.infrastructure.ledger import canonical, digest
from trace_coordinator.tool.implementations.fixture import QueryInput


class TypeScriptMappingInspector:
    """Pinned Git blobs + trusted compiler; unsupported syntax fails closed."""

    def __init__(self, application: ApplicationConfig, config: MappingConfig) -> None:
        from trace_impact.ingestion.code import typescript_analyzer

        from trace_coordinator.tool.dependencies.git_changes import git

        self.app, self.config = application, config
        self.revision = getattr(application, config.environment).revision
        self.compiler = Path(typescript_analyzer.__file__).parent / "typescript/node_modules/typescript"
        self.wrappers: list[JsonObject] = [
            as_json_object(
                {
                    **b.model_dump(),
                    "source": git(application, "show", f"{self.revision}:{b.path}").decode("utf-8-sig"),
                }
            )
            for b in config.components
        ]

    def inspect(
        self,
        candidate: SourceInspectionCandidatePayload,
        element: ObservedElementPayload,
    ) -> SourceProvenancePayload:
        from trace_coordinator.tool.dependencies.git_changes import git

        deployed = git(
            self.app,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            self.app.baseline.revision,
            self.app.patched.revision,
            "--",
        )
        if deployed.decode() != candidate["patch"]:
            raise ValueError("Saved diff differs from pinned deployment comparison")
        source = git(self.app, "show", f"{self.revision}:{candidate['code_path']}").decode("utf-8-sig")
        lines = source.splitlines()
        if (
            not 1 <= candidate["line"] <= len(lines)
            or lines[candidate["line"] - 1].strip() != candidate["source_excerpt"]
        ):
            raise ValueError("Candidate source excerpt differs from pinned Git source")
        payload = dict(
            source=source,
            path=candidate["code_path"],
            label=candidate["label"],
            line=candidate["line"],
            native_tag=element["tag"],
            observed_type=element["type"],
            wrappers=self.wrappers,
        )
        result = subprocess.run(
            [
                "node",
                str(Path(__file__).resolve().parents[1] / "dependencies" / "validate_jsx.cjs"),
                str(self.compiler),
            ],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise ValueError("Static JSX validation failed; unsupported or ambiguous control mapping")
        validated = as_json_object(json.loads(result.stdout))
        owner = validated.get("owner")
        owner_start_line = validated.get("owner_start_line")
        wrapper_path = validated.get("wrapper_path")
        if (
            not isinstance(owner, str)
            or not isinstance(owner_start_line, int)
            or (wrapper_path is not None and not isinstance(wrapper_path, str))
        ):
            raise ValueError("Static JSX validator returned an invalid provenance record")
        return {
            "owner": owner,
            "owner_start_line": owner_start_line,
            "path": candidate["code_path"],
            "line": candidate["line"],
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "wrapper_path": wrapper_path,
            "wrapper_sha256": next(
                (
                    hashlib.sha256(wrapper_source.encode()).hexdigest()
                    for b in self.wrappers
                    if wrapper_path is not None
                    and b.get("path") == wrapper_path
                    and isinstance((wrapper_source := b.get("source")), str)
                ),
                None,
            ),
        }


def prepare_ui_snapshot(
    config_path: str | Path,
) -> tuple[MappingConfig, ApplicationConfig, Any, JsonObject]:
    """Pure local preparation. No credentials, network, LLM calls, or graph writes."""
    from trace_impact.ingestion.code.config import TypeScriptOptions, load_code_config
    from trace_impact.ingestion.code.typescript_analyzer import TypeScriptAnalyzer
    from trace_impact.shared.graph_models import GraphSnapshot

    from trace_coordinator.application.mapping import MappingConfig, project_snapshot, validate_mappings
    from trace_coordinator.domain.project import load_application

    config_path = Path(config_path).resolve()
    config = MappingConfig.model_validate_json(config_path.read_text(encoding="utf-8-sig"))
    config = config.model_copy(
        update={
            name: str((config_path.parent / getattr(config, name)).resolve())
            for name in (
                "application_file",
                "report_file",
                "code_config_file",
                "env_file",
                "output_directory",
            )
        }
    )
    app = load_application(Path(config.application_file))
    raw_report = Path(config.report_file).read_bytes()
    if hashlib.sha256(raw_report).hexdigest() != config.report_sha256:
        raise ValueError("Report differs from configured review hash")
    code_config = load_code_config(config.code_config_file)
    revision = getattr(app, config.environment).revision
    if (
        code_config.project_id != app.project_id
        or code_config.revision != revision
        or (Path(code_config.repository_path) != Path(app.repository_path))
        or code_config.analyzer.provider != "typescript"
    ):
        raise ValueError("Code configuration differs from selected deployment")
    report = TypeAdapter(AnalysisReportPayload).validate_json(raw_report)
    mappings = validate_mappings(report, config, app, TypeScriptMappingInspector(app, config))
    code = TypeScriptAnalyzer(TypeScriptOptions.model_validate(code_config.analyzer.options)).analyze(
        code_config
    )
    snapshot = GraphSnapshot.model_validate(
        project_snapshot(code.model_dump(mode="json"), mappings, config, app.project_id, revision)
    )
    root = Path(config.output_directory)
    prepared = as_json_object(
        {
            "status": "PREPARED",
            "snapshot": save_artifact(
                root, "prepared", snapshot.model_dump_json(indent=2).encode(), ".graph.json"
            ),
            "graph_id": snapshot.id,
            "code_graph_id": code.id,
            "report_sha256": config.report_sha256,
            "mappings": mappings,
            "llm_calls": 0,
            "behavior_verification": "NOT_RUN",
        }
    )
    save_artifact(root, "prepared", canonical(prepared).encode(), ".validation.json")
    return config, app, snapshot, prepared


def publish_ui_snapshot(config_path: str | Path) -> tuple[JsonObject, ArtifactPayload]:
    """Publish atomically in Neo4j, then verify traversal before a committed receipt.

    Repeating an interrupted operation uses the same immutable snapshot identity.
    A missing receipt never means that a timed-out remote transaction rolled back.
    """
    from dotenv import load_dotenv
    from trace_impact import Settings, create_pipeline
    from trace_impact.shared.graph_models import ImpactQuery

    config, app, snapshot, prepared = prepare_ui_snapshot(config_path)
    load_dotenv(config.env_file, override=False)
    mappings = TypeAdapter(list[ValidatedMappingPayload]).validate_python(prepared["mappings"])
    paths = tuple(sorted({mapping["candidate"]["code_path"] for mapping in mappings}))
    expected_ui = {n.id for n in snapshot.nodes if n.kind == "UIElement"}
    expected_flows = {n.id for n in snapshot.nodes if n.kind == "UserFlow"}
    with create_pipeline(Settings.from_env()) as pipeline:
        publication = pipeline.publish_code_graph(snapshot)
        result = pipeline.retrieve_impact(
            snapshot.id,
            ImpactQuery(
                changed_files=paths,
                scope={
                    "project_id": app.project_id,
                    "revision": getattr(app, config.environment).revision,
                },
            ),
        )
        if (
            result.status != "OK"
            or set(result.ui_ids) != expected_ui
            or set(result.flow_ids) != expected_flows
            or result.requirement_ids
        ):
            raise ValueError(
                "Published graph failed UI/flow retrieval verification; committed receipt not written"
            )
    receipt = as_json_object(
        {
            **prepared,
            "status": "PUBLISHED_AND_VERIFIED",
            "publication": publication,
            "retrieval": result.model_dump(mode="json"),
        }
    )
    saved = save_artifact(
        Path(config.output_directory), "committed", canonical(receipt).encode(), ".publication.json"
    )
    return receipt, saved


class KnowledgeTool:
    allowed_agents = frozenset({"coordinator"})
    input_model = QueryInput

    def __init__(self, name: str, application: ApplicationConfig, artifact_root: Path) -> None:
        self.name, self.app, self.artifact_root = name, application, artifact_root
        self.description = (
            "Retrieve code dependency paths for the pinned PR. Missing UI links are reported explicitly."
            if name == "knowledge.graph"
            else "Retrieve relevant documentation from the pinned completed ingestion run."
        )
        files = [
            Path(application.graph_snapshot_file),
            Path(application.retrieval_config_file),
            Path(application.ingestion_run_directory) / "corpus.json",
            Path(application.ingestion_run_directory) / "vector-index.json",
        ]
        if application.head_graph_snapshot_file:
            files.append(Path(application.head_graph_snapshot_file))
        self.version = "trace-impact-adapter-v1:" + digest(
            {
                "application": application.model_dump(mode="json"),
                "inputs": {str(p): digest(json.loads(p.read_text(encoding="utf-8"))) for p in files},
            }
        )

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolResult:
        from trace_impact import Settings, create_pipeline

        if context.project_id != self.app.project_id or context.changes is None:
            raise ToolFailure("Knowledge retrieval requires a validated change set in this project")
        settings = Settings.from_env().model_copy(
            update={"qdrant_path": self.app.vector_directory, "model_retries": 0, "request_timeout": 45}
        )
        with create_pipeline(settings) as pipeline:
            if self.name == "knowledge.graph":
                return self.graph(pipeline, context)
            return self.documents(pipeline, QueryInput.model_validate(arguments), context)

    def graph(self, pipeline: Any, context: ToolContext) -> ToolResult:
        from trace_impact.shared.graph_models import GraphSnapshot, ImpactQuery

        changes = context.changes
        if changes is None:
            raise ToolFailure("Graph retrieval requires a validated change set")
        snapshot = GraphSnapshot.model_validate_json(
            Path(self.app.graph_snapshot_file).read_text(encoding="utf-8")
        )
        if any(
            n.project_id != context.project_id or n.revision != changes.analysis_base for n in snapshot.nodes
        ):
            raise ToolFailure("Code graph does not match the project's baseline deployment revision")
        baseline_files = [f.path for f in changes.files if f.status != "A"]
        gaps = []
        results = []
        sources = [{"graph_id": snapshot.id, "revision": changes.analysis_base}]
        if baseline_files:
            query = ImpactQuery(
                changed_files=tuple(baseline_files),
                scope={"project_id": context.project_id, "revision": changes.analysis_base},
            )
            results.append(pipeline.retrieve_impact(snapshot.id, query).model_dump(mode="json"))
        added = [f.path for f in changes.files if f.status == "A"]
        if self.app.head_graph_snapshot_file:
            head = GraphSnapshot.model_validate_json(
                Path(self.app.head_graph_snapshot_file).read_text(encoding="utf-8")
            )
            if any(
                n.project_id != context.project_id or n.revision != changes.analysis_head for n in head.nodes
            ):
                raise ToolFailure("Head graph does not match the patched deployment revision")
            sources.append({"graph_id": head.id, "revision": changes.analysis_head})
            head_files = tuple(f.path for f in changes.files if f.status != "D")
            if head_files:
                results.append(
                    pipeline.retrieve_impact(
                        head.id,
                        ImpactQuery(
                            changed_files=head_files,
                            scope={
                                "project_id": context.project_id,
                                "revision": changes.analysis_head,
                            },
                        ),
                    ).model_dump(mode="json")
                )
        else:
            gaps.append("Head code graph is not configured; new dependencies and added code are not covered.")
            if added:
                gaps.append("Added files have no baseline symbols: " + ", ".join(added))
        summary = []
        any_mapped = any(result["ui_ids"] or result["flow_ids"] for result in results)
        patched_attestation = context.runtime_attestations.get("patched", {})
        attested_head = isinstance(patched_attestation, dict) and (
            patched_attestation.get("revision") == changes.analysis_head
        )
        for result_index, result in enumerate(results):
            summary.append(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "code_files",
                        "ui_ids",
                        "flow_ids",
                        "requirement_ids",
                        "witness_paths",
                    )
                }
            )
            summary[-1]["mapped_entities"] = [
                {key: node[key] for key in ("id", "kind", "name", "revision", "properties")}
                for node in result["evidence_nodes"]
                if node["kind"] in {"UIElement", "UserFlow", "Requirement"}
            ]
            if result["ui_ids"]:
                gaps.append(
                    "UI links have static structural validation and an attested deployed revision; behavior is reported separately."
                    if attested_head and result_index == len(results) - 1
                    else "UI links identify structural impact candidates; runtime attribution and behavior are not verified."
                )
                if not result["requirement_ids"]:
                    gaps.append("Mapped UI flows have no confirmed requirement CHECKS links.")
            if result["status"] != "OK" and not any_mapped:
                gaps.append(
                    f"Graph status {result['status']}: code evidence does not establish confirmed UI/flow mappings."
                )
        saved = save_artifact(self.artifact_root, context.run_id, canonical(results).encode(), ".graph.json")
        return ToolResult(
            evidence=(
                Evidence(
                    id="graph:" + saved["sha256"][:20],
                    project_id=context.project_id,
                    kind="graph",
                    source="neo4j:" + ",".join(source["graph_id"] for source in sources),
                    summary=canonical(summary),
                    metadata={
                        "artifact": as_json_value(saved),
                        "graph_sources": as_json_value(sources),
                    },
                ),
            ),
            gaps=tuple(gaps),
        )

    def documents(self, pipeline: Any, arguments: QueryInput, context: ToolContext) -> ToolResult:
        from trace_impact.retrieval import RetrievalConfig

        run = Path(self.app.ingestion_run_directory)
        corpus = json.loads((run / "corpus.json").read_text(encoding="utf-8"))
        if corpus["project"]["project_id"] != context.project_id:
            raise ToolFailure("Document corpus belongs to another project")
        configured = json.loads(Path(self.app.retrieval_config_file).read_text(encoding="utf-8"))
        retrieval = RetrievalConfig.model_validate(
            {
                key: configured[key]
                for key in ("schema_version", "candidate_limit", "reranker", "selector")
                if key in configured
            }
        )
        result = pipeline.retrieve(run, arguments.query, retrieval)
        payload = result.model_dump(mode="json")
        saved = save_artifact(
            self.artifact_root, context.run_id, canonical(payload).encode(), ".retrieval.json"
        )
        selected = set(result.selection.ids)
        evidence = tuple(
            Evidence(
                id="document:" + passage.id + ":" + saved["sha256"][:8],
                project_id=context.project_id,
                kind="document",
                summary=passage.text,
                source=passage.artifact_path,
                metadata={
                    "chunk_id": passage.id,
                    "source_id": passage.source_id,
                    "run_id": passage.scope.run_id,
                    "artifact": as_json_value(saved),
                    "retrieval_score": passage.retrieval_score,
                },
            )
            for passage in result.candidates
            if passage.id in selected
        )
        return ToolResult(
            evidence=evidence,
            gaps=(
                "Retrieved documents are relevance evidence; requirement semantic approval remains separate.",
            ),
        )
