from ingestion.beans.decorators import component
from ingestion.domain.models import ConfirmedMapping, GraphRecord, GraphRelationship, UiObservation
from ingestion.mapping.interface import MappingResolver


@component(contract=MappingResolver, name="none")
class DisabledMappingResolver:
    def resolve(
        self,
        code_records: tuple[GraphRecord, ...],
        ui_observations: tuple[UiObservation, ...],
        code_relationships: tuple[GraphRelationship, ...] = (),
    ) -> tuple[ConfirmedMapping, ...]:
        del code_records, ui_observations, code_relationships
        return ()
