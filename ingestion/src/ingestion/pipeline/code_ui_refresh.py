"""Refresh code relationships and browser mappings without model or vector calls."""

from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from ingestion.config_loader.models import ApplicationConfig
from ingestion.domain.models import CodeUiRefreshResult, GraphRecord, UiObservation
from ingestion.extractor.code.interface import CodeExtractor
from ingestion.extractor.ui.interface import UiExtractor
from ingestion.mapping.interface import MappingResolver
from ingestion.mapping.route_observation import observed_route_relationships
from ingestion.storage.artifacts.interface import ArtifactStore
from ingestion.storage.interface import GraphStore


class CodeUiRefreshPipeline:
    """Rebuild static imports and observed route mappings, then publish them."""

    def __init__(
        self,
        config: ApplicationConfig,
        code_extractor: CodeExtractor,
        ui_extractor: UiExtractor,
        graph_store: GraphStore,
        mapping_resolver: MappingResolver,
        artifact_store: ArtifactStore,
    ) -> None:
        self._config = config
        self._code_extractor = code_extractor
        self._ui_extractor = ui_extractor
        self._graph_store = graph_store
        self._mapping_resolver = mapping_resolver
        self._artifact_store = artifact_store

    def run(self) -> CodeUiRefreshResult:
        code_config = self._config.input.code
        ui_config = self._config.input.ui
        if code_config is None or not code_config.enabled:
            raise ValueError("Code/UI refresh requires enabled input.code configuration")
        if ui_config is None or not ui_config.enabled:
            raise ValueError("Code/UI refresh requires enabled input.ui configuration")

        code_graph = self._code_extractor.extract(code_config)
        observations = self._ui_extractor.extract(ui_config)
        ui_records = self._ui_records(observations)
        mappings = self._mapping_resolver.resolve(
            code_graph.nodes, observations, code_graph.relationships
        )
        if len(mappings) > self._config.constraints.limits.confirmed_mappings:
            raise ValueError("Confirmed mapping limit exceeded")

        self._graph_store.replace_code_graph(code_graph.nodes, code_graph.relationships)
        self._graph_store.save(ui_records)
        self._graph_store.save_relationships(
            observed_route_relationships(code_graph.nodes, observations)
        )
        if self._config.storage.graph.publish_confirmed_mappings and observations:
            self._graph_store.replace_mappings(
                tuple(observation.id for observation in observations), mappings
            )

        result = CodeUiRefreshResult(
            refresh_id=uuid4().hex,
            revision=code_config.revision,
            code_files=sum(record.kind == "CodeFile" for record in code_graph.nodes),
            import_relationships=sum(
                relationship.kind == "IMPORTS" for relationship in code_graph.relationships
            ),
            ui_observations=len(observations),
            ready_ui_observations=sum(observation.content_ready for observation in observations),
            confirmed_mappings=len(mappings),
            refreshed_at=datetime.now(UTC),
        )
        self._artifact_store.save_code_ui_refresh(result, code_graph, ui_records, observations, mappings)
        return result

    def __enter__(self) -> "CodeUiRefreshPipeline":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._graph_store.close()

    @staticmethod
    def _ui_records(observations: tuple[UiObservation, ...]) -> tuple[GraphRecord, ...]:
        return tuple(
            GraphRecord(
                id=observation.id,
                kind="UiPage",
                properties={
                    "url": observation.url,
                    "route_path": urlsplit(observation.url).path,
                    "query_parameter_names": ",".join(observation.query_parameter_names),
                    "title": observation.page_title,
                    "content_sha256": observation.content_sha256,
                    "http_status": str(observation.http_status),
                    "captured": str(observation.captured).lower(),
                    "content_ready": str(observation.content_ready).lower(),
                },
            )
            for observation in observations
        )
