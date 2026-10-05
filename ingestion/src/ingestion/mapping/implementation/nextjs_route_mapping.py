"""Confirmed UI mappings derived from Next.js App Router conventions."""

import hashlib
import re
from collections import defaultdict, deque
from urllib.parse import urlparse

from ingestion.beans.decorators import component
from ingestion.domain.models import ConfirmedMapping, GraphRecord, GraphRelationship, UiObservation
from ingestion.mapping.interface import MappingResolver

ROUTE_GROUP = re.compile(r"^\(.+\)$")
DYNAMIC_SEGMENT = re.compile(r"^\[.+\]$")


@component(contract=MappingResolver, name="nextjs_routes")
class NextJsRouteMappingResolver:
    def resolve(
        self,
        code_records: tuple[GraphRecord, ...],
        ui_observations: tuple[UiObservation, ...],
        code_relationships: tuple[GraphRelationship, ...] = (),
    ) -> tuple[ConfirmedMapping, ...]:
        route_files = tuple(
            record
            for record in code_records
            if record.properties.get("path", "").startswith("src/app/")
            and record.properties.get("path", "").endswith(
                ("/page.ts", "/page.tsx", "/page.js", "/page.jsx")
            )
        )
        mappings: dict[str, ConfirmedMapping] = {}
        mapping_evidence: dict[str, tuple[str, ...]] = {}
        records_by_id = {record.id: record for record in code_records}
        known_ids = set(records_by_id)
        imported_by_file: dict[str, list[GraphRelationship]] = defaultdict(list)
        route_by_page: dict[str, str] = {}
        parameters_by_route: dict[str, set[str]] = defaultdict(set)
        for relationship in code_relationships:
            if relationship.kind == "IMPORTS":
                imported_by_file[relationship.source_id].append(relationship)
            elif relationship.kind == "DECLARES_ROUTE":
                route_by_page[relationship.source_id] = relationship.target_id
            elif relationship.kind == "ACCEPTS_QUERY_PARAMETER":
                parameter = records_by_id.get(relationship.target_id)
                name = parameter.properties.get("name") if parameter is not None else None
                if isinstance(name, str):
                    parameters_by_route[relationship.source_id].add(name)
        for observation in ui_observations:
            if (
                not observation.captured
                or not observation.evidence_ids
                or not observation.content_sha256
            ):
                continue
            sources = set(observation.confirmed_code_ids)
            matching_routes = tuple(
                record.id
                for record in route_files
                if self._matches(record.properties["path"], urlparse(observation.url).path)
                and self._query_parameters_match(
                    record.id,
                    observation,
                    route_by_page,
                    parameters_by_route,
                )
            )
            if matching_routes:
                route_by_id = {record.id: record for record in route_files}
                most_specific = max(
                    self._specificity(route_by_id[route_id].properties["path"])
                    for route_id in matching_routes
                )
                page_sources = {
                    route_id
                    for route_id in matching_routes
                    if self._specificity(route_by_id[route_id].properties["path"]) == most_specific
                }
                sources.update(page_sources)
                for page_source in page_sources:
                    queue: deque[tuple[str, tuple[str, ...]]] = deque([(page_source, ())])
                    visited = {page_source}
                    while queue:
                        current_id, edge_ids = queue.popleft()
                        for edge in imported_by_file.get(current_id, []):
                            if edge.target_id in visited:
                                continue
                            visited.add(edge.target_id)
                            path_edge_ids = (*edge_ids, edge.id)
                            sources.add(edge.target_id)
                            mapping_evidence[edge.target_id] = path_edge_ids
                            queue.append((edge.target_id, path_edge_ids))
            unknown = sources - known_ids
            if unknown:
                raise ValueError(f"Confirmed mappings reference unknown code IDs: {sorted(unknown)}")
            for source_id in sources:
                source = records_by_id[source_id]
                source_hash = source.properties.get("sha256")
                if not source_hash:
                    continue
                evidence_ids = tuple(
                    sorted(
                        {
                            *observation.evidence_ids,
                            f"code-sha256:{source_hash}",
                            *(f"code-edge:{edge_id}" for edge_id in mapping_evidence.get(source_id, ())),
                        }
                    )
                )
                identifier = hashlib.sha256(
                    f"{source_id}:AFFECTS_UI:{observation.id}".encode()
                ).hexdigest()
                mappings[identifier] = ConfirmedMapping(
                    id=identifier,
                    source_id=source_id,
                    target_id=observation.id,
                    confidence=1.0,
                    basis=(
                        "component_tag"
                        if source_id in observation.confirmed_code_ids
                        else "static_import_reachability"
                        if source_id in mapping_evidence
                        else "framework_route"
                    ),
                    evidence_ids=evidence_ids,
                )
        return tuple(mappings.values())

    @staticmethod
    def _query_parameters_match(
        page_id: str,
        observation: UiObservation,
        route_by_page: dict[str, str],
        parameters_by_route: dict[str, set[str]],
    ) -> bool:
        if not observation.query_parameter_names or page_id not in route_by_page:
            return True
        known_parameters = parameters_by_route.get(route_by_page[page_id], set())
        return set(observation.query_parameter_names).issubset(known_parameters)

    @staticmethod
    def _matches(file_path: str, url_path: str) -> bool:
        relative = file_path.removeprefix("src/app/")
        route = re.sub(r"(^|/)page\.(?:tsx?|jsx?)$", "", relative)
        route_segments = [
            segment for segment in route.split("/") if segment and not ROUTE_GROUP.match(segment)
        ]
        url_segments = [segment for segment in url_path.split("/") if segment]
        if len(route_segments) != len(url_segments):
            return False
        return all(
            DYNAMIC_SEGMENT.match(route_segment) or route_segment == url_segment
            for route_segment, url_segment in zip(route_segments, url_segments, strict=True)
        )

    @staticmethod
    def _specificity(file_path: str) -> int:
        relative = file_path.removeprefix("src/app/")
        route = re.sub(r"(^|/)page\.(?:tsx?|jsx?)$", "", relative)
        return sum(
            1
            for segment in route.split("/")
            if segment and not ROUTE_GROUP.match(segment) and not DYNAMIC_SEGMENT.match(segment)
        )
