"""Validate observed UI mappings before projecting them into an immutable graph.

No model calls and no agent tool loop: this is an explicit ingestion operation.
An inspector can be replaced without changing evidence validation or publication.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import Field

from trace_coordinator.ledger import digest
from trace_coordinator.models import Record
from trace_coordinator.ui_evidence import ui_index


class ComponentBinding(Record):
    module: str = Field(description="Exact import specifier in the consuming TSX file.")
    export: str
    path: str = Field(description="Repository-relative wrapper source at the pinned revision.")
    native_tag: Literal["input", "button"]


class MappingConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    provider: Literal["typescript_jsx"] = "typescript_jsx"
    application_file: str
    report_file: str
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", description="Pins the reviewed exploration report.")
    code_config_file: str
    env_file: str
    output_directory: str
    environment: Literal["baseline", "patched"] = "patched"
    flow_name: str = Field(min_length=1, max_length=200)
    components: tuple[ComponentBinding, ...] = Field(default=(), max_length=50)
    max_candidates: int = Field(default=50, ge=1, le=50)


class SourceInspector(Protocol):
    def inspect(self, candidate: dict, element: dict) -> dict:
        """Return owner identity and static JSX provenance, or raise ValueError."""
        ...


def verified_bytes(reference):
    data = Path(reference["path"]).read_bytes()
    if len(data) != reference["bytes"] or hashlib.sha256(data).hexdigest() != reference["sha256"]:
        raise ValueError("Evidence artifact hash/size mismatch")
    return data


def validate_mappings(report, config, application, inspector: SourceInspector):
    """Fail the whole batch closed; no partial confirmation after a rejected link."""
    from trace_coordinator.models import Evidence

    evidence = {key: Evidence.model_validate(value) for key, value in report["evidence"].items()}
    if any(key != value.id or value.project_id != application.project_id for key, value in evidence.items()):
        raise ValueError("Evidence identity or project mismatch")
    rebuilt = ui_index({key: value.model_dump(mode="json") for key, value in evidence.items()})
    if rebuilt != report["ui_knowledge"]:
        raise ValueError("UI index does not match its underlying evidence")
    candidates = [c for c in rebuilt["code_ui_candidates"] if c["environment"] == config.environment]
    if not candidates or len(candidates) > config.max_candidates or rebuilt["candidates_truncated"]:
        raise ValueError("No complete, bounded candidate batch")
    deployment = getattr(application, config.environment)
    observations = {}
    for item in evidence.values():
        if item.kind != "browser" or item.metadata["environment"] != config.environment:
            continue
        for key in ("observation", "dom", "screenshot"):
            verified_bytes(item.metadata[key])
        observation = json.loads(verified_bytes(item.metadata["observation"]))
        if observation != json.loads(item.summary):
            raise ValueError("Observation summary differs from saved capture")
        url = urlsplit(observation["url"])
        if (
            observation["claimed_revision"] != deployment.revision
            or observation["environment"] != config.environment
            or f"{url.scheme}://{url.netloc}" != deployment.url
        ):
            raise ValueError("Observation deployment scope mismatch")
        if "transition" in item.metadata:
            if json.loads(verified_bytes(item.metadata["transition"])) != item.metadata["transition_record"]:
                raise ValueError("Transition differs from saved artifact")
        observations[item.id] = observation
    paths = [p for p in rebuilt["discovered_paths"] if p["environment"] == config.environment]
    validated, seen = [], set()
    for candidate in candidates:
        if candidate["configured_revision"] != deployment.revision:
            raise ValueError("Candidate revision mismatch")
        identity = (candidate["browser_evidence_id"], candidate["element_id"])
        if identity in seen:
            raise ValueError("Ambiguous source mapping for observed element")
        seen.add(identity)
        observation = observations[candidate["browser_evidence_id"]]
        matches = [e for e in observation["elements"] if e["name"] == candidate["label"]]
        if len(matches) != 1 or matches[0]["id"] != candidate["element_id"]:
            raise ValueError("Observed label is ambiguous or mismatched")
        flow_matches = [p for p in paths if p["transitions"][-1]["to"] == candidate["browser_evidence_id"]]
        if len(flow_matches) != 1:
            raise ValueError("Mapped control must belong to one observed terminal flow state")
        for transition in flow_matches[0]["transitions"]:
            before = observations[transition["from"]]
            if transition["action"] != "click" or not any(
                e["id"] == transition["element_id"] and e["name"] == transition["element_name"]
                for e in before["elements"]
            ):
                raise ValueError("Flow action is not supported by its source observation")
        diff = evidence[candidate["diff_evidence_id"]]
        patch = verified_bytes(diff.metadata["artifact"])
        provenance = inspector.inspect({**candidate, "patch": patch.decode()}, matches[0])
        validated.append(
            {
                "candidate": candidate,
                "source": provenance,
                "observed_element": matches[0],
                "url": observation["url"],
                "flow": flow_matches[0],
                "validation": "STATIC_JSX_AND_OBSERVED_CONTROL",
                "runtime_attribution_verified": False,
                "behavior_verification": "NOT_RUN",
            }
        )
    return validated


def project_snapshot(code, mappings, config, project_id, revision):
    """Existing code snapshot stays untouched; only structural edges are confirmed."""
    if not code["nodes"] or any(
        n["project_id"] != project_id
        or n["revision"] != revision
        or n["kind"] not in {"CodeFile", "CodeSymbol"}
        for n in code["nodes"]
    ):
        raise ValueError("Expected a pure code snapshot for the selected revision")
    nodes = {n["id"]: n for n in code["nodes"]}
    edges = list(code["edges"])
    scope = {"project_id": project_id, "revision": revision}
    for mapping in mappings:
        candidate, source = mapping["candidate"], mapping["source"]
        owners = [
            n
            for n in code["nodes"]
            if n["kind"] == "CodeSymbol"
            and n["name"] == source["owner"]
            and n["properties"]["path"] == candidate["code_path"]
            and n["properties"]["start_line"] == source["owner_start_line"]
            and n["properties"]["sha256"] == source["source_sha256"]
        ]
        if len(owners) != 1:
            raise ValueError("JSX owner does not uniquely match the pinned code graph")
        ui_id = (
            "ui:"
            + digest(
                [scope, config.report_sha256, candidate["browser_evidence_id"], candidate["element_id"]]
            )[:28]
        )
        flow_id = "flow:" + digest([scope, config.report_sha256, mapping["flow"]])[:28]
        provenance = {
            "report_sha256": config.report_sha256,
            "validation": mapping["validation"],
            "runtime_attribution_verified": False,
            "behavior_verification": "NOT_RUN",
        }
        nodes[ui_id] = dict(
            id=ui_id,
            kind="UIElement",
            name=candidate["label"],
            **scope,
            properties={
                **provenance,
                "url": mapping["url"],
                "source": source,
                "browser_evidence_id": candidate["browser_evidence_id"],
                "element": mapping["observed_element"],
            },
        )
        nodes[flow_id] = dict(
            id=flow_id,
            kind="UserFlow",
            name=config.flow_name,
            **scope,
            properties={
                **provenance,
                "transitions": mapping["flow"]["transitions"],
                "coverage": "OBSERVED_PATH_ONLY",
            },
        )
        for edge_type, start in (("RENDERS", owners[0]["id"]), ("CONTAINS", flow_id)):
            edges.append(
                dict(
                    id=digest([edge_type, start, ui_id])[:28],
                    type=edge_type,
                    source=start,
                    target=ui_id,
                    status="CONFIRMED",
                    properties=provenance,
                )
            )
    payload = dict(
        origin="STATIC_UI_MAPPING_NOT_RUNTIME_OR_BEHAVIOR_PROOF",
        nodes=list(nodes.values()),
        edges=edges,
        diagnostics=[*code["diagnostics"], "No requirement CHECKS links; behavior not tested."],
    )
    return {"id": digest(payload)[:28], **payload}
