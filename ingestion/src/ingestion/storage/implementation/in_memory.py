from ingestion.beans.decorators import component
from ingestion.domain.models import ConfirmedMapping, GraphRecord, GraphRelationship, VectorRecord
from ingestion.storage.interface import GraphStore, VectorStore


@component(contract=VectorStore, name="memory")
class InMemoryVectorStore:
    def __init__(self) -> None:
        self.records: dict[str, VectorRecord] = {}

    def save(self, records: tuple[VectorRecord, ...]) -> int:
        self.records.update({record.id: record for record in records})
        return len(records)

    def replace_code_records(self, records: tuple[VectorRecord, ...]) -> int:
        self.records = {
            identifier: record
            for identifier, record in self.records.items()
            if record.metadata.get("kind") != "code"
        }
        self.records.update({record.id: record for record in records})
        return len(records)

    def close(self) -> None:
        return None


@component(contract=GraphStore, name="memory")
class InMemoryGraphStore:
    def __init__(self) -> None:
        self.records: dict[str, GraphRecord] = {}
        self.mappings: dict[str, ConfirmedMapping] = {}
        self.relationships: dict[str, GraphRelationship] = {}

    def save(self, records: tuple[GraphRecord, ...]) -> int:
        self.records.update({record.id: record for record in records})
        return len(records)

    def save_mappings(self, mappings: tuple[ConfirmedMapping, ...]) -> int:
        self.mappings.update({mapping.id: mapping for mapping in mappings})
        return len(mappings)

    def replace_mappings(
        self, target_ids: tuple[str, ...], mappings: tuple[ConfirmedMapping, ...]
    ) -> int:
        targets = set(target_ids)
        self.mappings = {
            identifier: mapping
            for identifier, mapping in self.mappings.items()
            if mapping.target_id not in targets
        }
        return self.save_mappings(mappings)

    def save_relationships(self, relationships: tuple[GraphRelationship, ...]) -> int:
        self.relationships.update({item.id: item for item in relationships})
        return len(relationships)

    def replace_code_graph(
        self,
        records: tuple[GraphRecord, ...],
        relationships: tuple[GraphRelationship, ...],
    ) -> int:
        old_code_ids = {
            identifier
            for identifier, record in self.records.items()
            if record.kind in {"CodeFile", "CodeSymbol"}
        }
        self.records = {
            identifier: record
            for identifier, record in self.records.items()
            if record.kind not in {"CodeFile", "CodeSymbol"}
        }
        self.relationships = {
            identifier: relationship
            for identifier, relationship in self.relationships.items()
            if relationship.source_id not in old_code_ids
            and relationship.target_id not in old_code_ids
        }
        self.records.update({record.id: record for record in records})
        self.relationships.update({item.id: item for item in relationships})
        return len(records) + len(relationships)

    def close(self) -> None:
        return None
