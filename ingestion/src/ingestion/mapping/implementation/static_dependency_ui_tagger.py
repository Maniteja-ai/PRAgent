"""Derive candidate routes from Next.js pages and static import reachability."""

from collections import defaultdict, deque
from pathlib import PurePosixPath

from ingestion.beans.decorators import component
from ingestion.config_loader.models import UiTaggingConfig
from ingestion.domain.models import CodeGraph, RawDocument
from ingestion.mapping.ui_candidate_tagger import UiCandidateTagger


@component(contract=UiCandidateTagger, name="static_dependencies")
class StaticDependencyUiTagger:
    """Tag code chunks with likely routes without publishing confirmed mappings."""

    def tag(self, graph: CodeGraph, config: UiTaggingConfig) -> CodeGraph:
        if not config.enabled:
            return graph

        paths = {
            node.id: str(node.properties.get("path", ""))
            for node in graph.nodes
            if node.kind == "CodeFile"
        }
        pages = {
            node_id: route
            for node_id, path in paths.items()
            if (route := self._route_for_page(path)) is not None
        }
        dependents: dict[str, set[str]] = defaultdict(set)
        for relationship in graph.relationships:
            if relationship.kind == "IMPORTS" and (
                relationship.source_id in paths and relationship.target_id in paths
            ):
                dependents[relationship.source_id].add(relationship.target_id)

        routes_by_file: dict[str, set[str]] = defaultdict(set)
        for page_id, route in pages.items():
            queue = deque([(page_id, 0)])
            visited = {page_id}
            while queue:
                file_id, hops = queue.popleft()
                routes_by_file[file_id].add(route)
                if hops >= config.max_dependency_hops:
                    continue
                for dependency_id in dependents[file_id] - visited:
                    visited.add(dependency_id)
                    queue.append((dependency_id, hops + 1))

        documents = tuple(
            self._tag_document(document, routes_by_file, config)
            for document in graph.source_documents
        )
        return graph.model_copy(update={"source_documents": documents})

    @staticmethod
    def _tag_document(
        document: RawDocument,
        routes_by_file: dict[str, set[str]],
        config: UiTaggingConfig,
    ) -> RawDocument:
        file_id = document.metadata.get("code_file_id")
        if not isinstance(file_id, str):
            return document
        routes = tuple(sorted(routes_by_file.get(file_id, ())))[: config.max_routes_per_file]
        path = document.metadata.get("path")
        tags = StaticDependencyUiTagger._path_tags(path if isinstance(path, str) else "")
        metadata = {
            **document.metadata,
            "ui_route_candidates": routes,
            "ui_tags": tags,
            "ui_mapping_status": "candidate" if routes else "unmapped",
            "ui_mapping_basis": "static_import_reachability",
        }
        return document.model_copy(update={"metadata": metadata})

    @staticmethod
    def _route_for_page(path: str) -> str | None:
        pure_path = PurePosixPath(path)
        if "app" not in pure_path.parts or pure_path.name not in {
            "page.tsx", "page.ts", "page.jsx", "page.js"
        }:
            return None
        app_index = pure_path.parts.index("app")
        segments = pure_path.parts[app_index + 1 : -1]
        route_segments = [
            "{" + segment[1:-1] + "}" if segment.startswith("[") and segment.endswith("]") else segment
            for segment in segments
            if not (segment.startswith("(") and segment.endswith(")"))
        ]
        return "/" + "/".join(route_segments)

    @staticmethod
    def _path_tags(path: str) -> tuple[str, ...]:
        stem = PurePosixPath(path).stem
        parts = (*PurePosixPath(path).parts[:-1], stem)
        return tuple(
            sorted(
                {
                    token.lower()
                    for part in parts
                    for token in part.replace("_", "-").split("-")
                    if token and token not in {"src", "app", "components", "component"}
                    and not (token.startswith("[") and token.endswith("]"))
                    and not (token.startswith("(") and token.endswith(")"))
                }
            )
        )
