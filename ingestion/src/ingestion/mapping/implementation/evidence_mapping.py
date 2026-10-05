from ingestion.beans.decorators import component
from ingestion.domain.models import ConfirmedMapping, GraphRecord, GraphRelationship, UiObservation
from ingestion.mapping.interface import MappingResolver


@component(contract=MappingResolver, name="evidence")
class EvidenceMappingResolver:
    """Publishes mappings only when UI evidence names a known code entity."""

    def resolve(
        self,
        code_records: tuple[GraphRecord, ...],
        ui_observations: tuple[UiObservation, ...],
        code_relationships: tuple[GraphRelationship, ...] = (),
    ) -> tuple[ConfirmedMapping, ...]:
        del code_relationships
        known_code_ids = {record.id for record in code_records}
        mappings = tuple(
            ConfirmedMapping(
                id=f"mapping:{code_id}:{observation.id}",
                source_id=code_id,
                target_id=observation.id,
                confidence=1.0,
                basis="component_tag",
                evidence_ids=observation.evidence_ids,
            )
            for observation in ui_observations
            if observation.evidence_ids
            for code_id in observation.confirmed_code_ids
        )
        unknown_sources = {
            mapping.source_id for mapping in mappings if mapping.source_id not in known_code_ids
        }
        if unknown_sources:
            raise ValueError(f"Confirmed mappings reference unknown code IDs: {sorted(unknown_sources)}")
        return mappings
