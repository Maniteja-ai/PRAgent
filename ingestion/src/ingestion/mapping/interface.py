from typing import Protocol

from ingestion.domain.models import ConfirmedMapping, GraphRecord, GraphRelationship, UiObservation


class MappingResolver(Protocol):
    def resolve(
        self,
        code_records: tuple[GraphRecord, ...],
        ui_observations: tuple[UiObservation, ...],
        code_relationships: tuple[GraphRelationship, ...] = (),
    ) -> tuple[ConfirmedMapping, ...]: ...
