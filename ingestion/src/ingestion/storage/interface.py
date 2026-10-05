from typing import Protocol

from ingestion.domain.models import ConfirmedMapping, GraphRecord, GraphRelationship, VectorRecord


class VectorStore(Protocol):
    def save(self, records: tuple[VectorRecord, ...]) -> int: ...

    def replace_code_records(self, records: tuple[VectorRecord, ...]) -> int: ...

    def close(self) -> None: ...


class GraphStore(Protocol):
    def save(self, records: tuple[GraphRecord, ...]) -> int: ...

    def save_relationships(self, relationships: tuple[GraphRelationship, ...]) -> int: ...

    def replace_code_graph(
        self,
        records: tuple[GraphRecord, ...],
        relationships: tuple[GraphRelationship, ...],
    ) -> int: ...

    def save_mappings(self, mappings: tuple[ConfirmedMapping, ...]) -> int: ...

    def replace_mappings(
        self, target_ids: tuple[str, ...], mappings: tuple[ConfirmedMapping, ...]
    ) -> int: ...

    def close(self) -> None: ...
