"""Deterministic code-to-UI mapping through stable ``data-impact-id`` tags."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field

from trace_impact.ingestion.mappings.models import (
    CoverageGap,
    EntityReference,
    MappingBatch,
    MappingCandidate,
    MappingEvidence,
)
from trace_impact.ingestion.models import stable_id
from trace_impact.shared.contracts import Contract
from trace_impact.shared.graph_models import GraphEdge, GraphNode, GraphSnapshot

IMPACT_ID_PATTERN = r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$"


class ObservedImpactElement(Contract):
    """One visible DOM element captured by a browser adapter."""

    impact_id: str = Field(pattern=IMPACT_ID_PATTERN)
    tag: str = Field(min_length=1)
    name: str = ""


class UiObservation(Contract):
    """Attested browser output for one project revision and page."""

    project_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    url: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_build_attested: Literal[True]
    elements: tuple[ObservedImpactElement, ...]


class StableTagMappingResult(Contract):
    mappings: MappingBatch
    graph: GraphSnapshot


class StableImpactTagMapper:
    """Join static source ownership to observed DOM elements without an LLM."""

    validator_id: Literal["stable-impact-tag-validator-v1"] = "stable-impact-tag-validator-v1"

    def map(
        self,
        *,
        run_id: str,
        project_id: str,
        code: GraphSnapshot,
        observation: UiObservation,
    ) -> StableTagMappingResult:
        self._validate_scope(project_id, code, observation)
        owners = self._source_owners(code)
        observed = Counter(element.impact_id for element in observation.elements)
        elements_by_id = {element.impact_id: element for element in observation.elements}
        candidates: list[MappingCandidate] = []
        gaps: list[CoverageGap] = []
        ui_nodes: list[GraphNode] = []
        render_edges: list[GraphEdge] = []

        for impact_id in sorted(owners.keys() | observed.keys()):
            owner = owners.get(impact_id)
            count = observed.get(impact_id, 0)
            affected = tuple(item for item in (owner.id if owner else None, impact_id) if item)
            if owner is None:
                gaps.append(
                    self._gap(project_id, impact_id, "Observed tag has no static source owner", affected)
                )
                continue
            if count == 0:
                gaps.append(
                    self._gap(project_id, impact_id, "Source tag was not observed in the UI", affected)
                )
                continue
            if count > 1:
                gaps.append(
                    self._gap(project_id, impact_id, "Observed tag is ambiguous on the page", affected)
                )
                continue

            element = elements_by_id[impact_id]
            ui_id = stable_id(project_id, observation.revision, "ui", impact_id)
            candidate_id = stable_id(project_id, run_id, "renders", owner.id, ui_id)
            code_hash = owner.properties.get("sha256")
            if not isinstance(code_hash, str):
                raise ValueError(f"Code owner {owner.id} has no source SHA-256")
            evidence = (
                MappingEvidence(
                    source="code",
                    reference=str(owner.properties["path"]),
                    sha256=code_hash,
                    detail=f"Static owner declares data-impact-id={impact_id}",
                ),
                MappingEvidence(
                    source="ui",
                    reference=observation.reference,
                    sha256=observation.sha256,
                    detail=f"Visible {element.tag} observed at {observation.url}",
                ),
            )
            candidates.append(
                MappingCandidate(
                    id=candidate_id,
                    source=EntityReference(kind="CodeSymbol", id=owner.id),
                    target=EntityReference(kind="UIElement", id=ui_id),
                    relationship="RENDERS",
                    confidence=1.0,
                    status="CONFIRMED",
                    evidence=evidence,
                    reviewed_by=self.validator_id,
                )
            )
            ui_nodes.append(
                GraphNode(
                    id=ui_id,
                    kind="UIElement",
                    name=element.name or impact_id,
                    project_id=project_id,
                    revision=observation.revision,
                    properties={
                        "impact_id": impact_id,
                        "tag": element.tag,
                        "url": observation.url,
                        "observation_sha256": observation.sha256,
                    },
                )
            )
            render_edges.append(
                GraphEdge(
                    id=stable_id(code.id, "RENDERS", owner.id, ui_id),
                    source=owner.id,
                    target=ui_id,
                    type="RENDERS",
                    status="CONFIRMED",
                    properties={
                        "method": self.validator_id,
                        "impact_id": impact_id,
                        "runtime_attribution_verified": True,
                    },
                )
            )

        batch = MappingBatch(
            run_id=run_id,
            project_id=project_id,
            candidates=tuple(candidates),
            coverage_gaps=tuple(gaps),
        )
        graph = GraphSnapshot(
            id=stable_id(code.id, observation.sha256, self.validator_id),
            origin=f"{code.origin}+{self.validator_id}",
            nodes=(*code.nodes, *ui_nodes),
            edges=(*code.edges, *render_edges),
            diagnostics=(
                *code.diagnostics,
                f"Stable UI mapping: {len(candidates)} confirmed; {len(gaps)} coverage gaps.",
            ),
        )
        return StableTagMappingResult(mappings=batch, graph=graph)

    @staticmethod
    def _validate_scope(project_id: str, code: GraphSnapshot, observation: UiObservation) -> None:
        if observation.project_id != project_id:
            raise ValueError("UI observation belongs to a different project")
        scopes = {(node.project_id, node.revision) for node in code.nodes}
        if scopes != {(project_id, observation.revision)}:
            raise ValueError("Code graph and UI observation must use the same project and revision")

    @staticmethod
    def _source_owners(code: GraphSnapshot) -> dict[str, GraphNode]:
        owners: dict[str, GraphNode] = {}
        for node in code.nodes:
            if node.kind != "CodeSymbol":
                continue
            raw = node.properties.get("impact_ids", [])
            if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
                raise ValueError(f"Code owner {node.id} has invalid impact_ids")
            for impact_id in raw:
                if impact_id in owners:
                    raise ValueError(f"Stable UI tag {impact_id} has multiple source owners")
                owners[impact_id] = node
        return owners

    @staticmethod
    def _gap(project_id: str, impact_id: str, reason: str, affected: tuple[str, ...]) -> CoverageGap:
        return CoverageGap(
            id=stable_id(project_id, "coverage-gap", impact_id, reason),
            area=f"stable-ui-tag:{impact_id}",
            missing_relationship="RENDERS",
            reason=reason,
            affected_entity_ids=affected,
        )
