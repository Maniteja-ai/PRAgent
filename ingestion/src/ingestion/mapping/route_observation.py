"""Build stable graph edges between route definitions and browser observations."""

import hashlib
from urllib.parse import urlsplit

from ingestion.domain.models import GraphRecord, GraphRelationship, UiObservation


def observed_route_relationships(
    code_records: tuple[GraphRecord, ...], observations: tuple[UiObservation, ...]
) -> tuple[GraphRelationship, ...]:
    routes = tuple(
        (str(record.properties.get("path")), record.id)
        for record in code_records
        if record.kind == "Route"
    )
    relationships: list[GraphRelationship] = []
    for observation in observations:
        if not observation.captured or not observation.evidence_ids:
            continue
        observed_path = urlsplit(observation.url).path
        matching_routes = [
            (path, route_id) for path, route_id in routes if _route_matches(path, observed_path)
        ]
        if not matching_routes:
            continue
        specificity = max(_route_specificity(path) for path, _ in matching_routes)
        matching_route_ids = tuple(
            route_id for path, route_id in matching_routes if _route_specificity(path) == specificity
        )
        for route_id in matching_route_ids:
            identifier = hashlib.sha256(
                f"{route_id}:OBSERVED_AS:{observation.id}".encode()
            ).hexdigest()
            relationships.append(
                GraphRelationship(
                    id=identifier,
                    source_id=route_id,
                    target_id=observation.id,
                    kind="OBSERVED_AS",
                    properties={
                        "query_parameter_names": ",".join(observation.query_parameter_names),
                        "evidence_ids": ",".join(observation.evidence_ids),
                    },
                )
            )
    return tuple(relationships)


def _route_matches(route_path: str, observed_path: str) -> bool:
    route_segments = [segment for segment in route_path.split("/") if segment]
    observed_segments = [segment for segment in observed_path.split("/") if segment]
    return len(route_segments) == len(observed_segments) and all(
        (segment.startswith("{") and segment.endswith("}")) or segment == observed
        for segment, observed in zip(route_segments, observed_segments, strict=True)
    )


def _route_specificity(route_path: str) -> int:
    return sum(
        not (segment.startswith("{") and segment.endswith("}"))
        for segment in route_path.split("/")
        if segment
    )
