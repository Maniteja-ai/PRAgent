"""Typed configuration definitions and metadata propagation, without a UI or model calls."""

import json
from typing import Literal

import jsonschema
import pytest
from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import QdrantClient

from tests.ingestion.test_library import DeterministicTestEmbeddings, config
from tests.integration.test_ingestion_e2e import RecordingGraph
from trace_impact import create_pipeline
from trace_impact.ingestion.config import Project
from trace_impact.ingestion.metadata import MetadataConfig, MetadataField
from trace_impact.ingestion.schema import project_schema
from trace_impact.ingestion.storage.neo4j_requirement_store import Neo4jRequirementStore
from trace_impact.ingestion.storage.qdrant_store import QdrantVectorStore
from trace_impact.shared.component_config import ComponentDefinition


@pytest.fixture
def pipeline():
    with create_pipeline() as app:
        yield app


def metadata():
    return {
        "fields": [
            {
                "name": "area",
                "title": "Product area",
                "type": "string",
                "required": True,
                "choices": ["catalog", "checkout"],
            }
        ],
        "defaults": {"area": "catalog"},
    }


def test_metadata_defaults_overrides_and_definitions(tmp_path):
    spec = MetadataConfig.model_validate(metadata())
    assert spec.for_source({}) == {"area": "catalog"}
    assert spec.for_source({"area": "checkout"}) == {"area": "checkout"}
    assert spec.fields[0].value_schema()["enum"] == ["catalog", "checkout"]
    for values in [{"unknown": 1}, {"area": "other"}, {"area": 123}]:
        with pytest.raises(ValueError):
            spec.for_source(values)
    spec.defaults.clear()
    with pytest.raises(ValueError, match="Required"):
        spec.for_source({})
    data = json.loads(config(tmp_path).read_text())
    data["sources"][0]["metadata"] = {"area": "checkout"}
    with pytest.raises(ValueError, match="Define project metadata"):
        Project.model_validate(data)
    data["metadata"] = metadata()
    assert Project.model_validate(data).sources[0].metadata == {"area": "checkout"}


@pytest.mark.parametrize(
    "field, good, bad",
    [
        ({"type": "number"}, 2.5, True),
        ({"type": "number"}, 0, float("inf")),
        ({"type": "boolean"}, False, 0),
        ({"type": "string_list"}, ["a"], [1]),
        ({"type": "string", "required": True}, "ok", "  "),
    ],
)
def test_metadata_value_types(field, good, bad):
    spec = MetadataConfig(fields=[MetadataField(name="value", title="Value", **field)])
    assert spec.for_source({"value": good})["value"] == good
    with pytest.raises(ValueError):
        spec.for_source({"value": bad})


@pytest.mark.parametrize(
    "change",
    [
        {"name": "run_id"},
        {"name": "constructor"},
        {"name": "__proto__"},
        {"choices": ["x", "x"]},
        {"type": "number", "choices": ["x"]},
    ],
)
def test_invalid_metadata_definitions_are_rejected(change):
    with pytest.raises(ValueError):
        MetadataField.model_validate({"name": "area", "title": "Area", **change})


def test_metadata_schema_supplies_choices_and_checks_source_overrides(tmp_path, pipeline):
    data = json.loads(config(tmp_path).read_text())
    data["metadata"] = metadata()
    spec = MetadataConfig.model_validate(data["metadata"])
    schema = project_schema(pipeline.components, spec)
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(data)
    props = schema["$defs"]["Source"]["properties"]["metadata"]["properties"]
    assert props["area"]["enum"] == ["catalog", "checkout"]
    data["sources"][0]["metadata"] = {"area": "wrong"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(data)
    data["sources"][0]["metadata"] = {"unknown": "checkout"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(data)
    data["metadata"]["defaults"] = {}
    spec.defaults.clear()
    validator = jsonschema.Draft202012Validator(project_schema(pipeline.components, spec))
    del data["sources"][0]["metadata"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(data)
    data["sources"][0]["metadata"] = {"area": "checkout"}
    validator.validate(data)


def test_required_plugin_options_cannot_be_omitted(tmp_path, pipeline):
    data = json.loads(config(tmp_path).read_text())
    data["sources"][0]["loader"] = "github_file"
    data["sources"][0].pop("options", None)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, project_schema(pipeline.components))
    with pytest.raises(ValueError):
        pipeline.validate_project(Project.model_validate(data))


def test_plugin_options_schema_matches_runtime_without_constructing_plugin(tmp_path, pipeline):
    class Settings(BaseModel):
        mode: Literal["brief", "full"] = Field(description="Extraction detail")

    class Options(BaseModel):
        model_config = ConfigDict(extra="forbid")
        settings: Settings

    def forbidden_factory():
        pytest.fail("Schema or validation must not construct the plugin")

    pipeline.components.loaders.register_factory(
        "custom",
        forbidden_factory,
        definition=ComponentDefinition("Custom extraction", "Read a custom input", Options),
    )
    data = json.loads(config(tmp_path).read_text())
    source = data["sources"][0]
    source.update(loader="custom", options={"settings": {"mode": "brief"}})
    schema = project_schema(pipeline.components)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(data, schema)
    pipeline.validate_project(Project.model_validate(data))
    assert schema["x-components"]["loaders"]["custom"]["title"] == "Custom extraction"
    source["options"]["settings"]["mode"] = "wrong"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, schema)
    with pytest.raises(ValueError):
        pipeline.validate_project(Project.model_validate(data))


def test_metadata_reaches_artifacts_vector_payloads_and_graph_parameters(tmp_path, pipeline):
    data = json.loads(config(tmp_path).read_text())
    data["metadata"] = metadata()
    data["sources"][0]["metadata"] = {"area": "checkout"}
    data["embedding_provider"] = "test_embeddings"
    data["storage"]["vector"] = "test_qdrant"
    client = QdrantClient(":memory:")
    embeddings = DeterministicTestEmbeddings()
    pipeline.components.embeddings.register("test_embeddings", embeddings)
    pipeline.components.vectors.register("test_qdrant", QdrantVectorStore(client))
    path = tmp_path / "project.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    folder, corpus = pipeline.collect(path, tmp_path / "runs")
    assert not corpus.errors
    assert all(s.metadata == {"area": "checkout"} for s in corpus.snapshots)
    assert all(c.metadata == {"area": "checkout"} for c in corpus.chunks)
    assert pipeline.index(folder).status == "COMPLETE"
    points, _ = client.scroll(QdrantVectorStore.collection(embeddings.profile), limit=100)
    assert len(points) == len(corpus.chunks)
    assert all(p.payload["metadata"] == {"area": "checkout"} for p in points)
    graph = RecordingGraph()
    Neo4jRequirementStore._load_tx(graph, corpus, None)
    snapshot_props = [p["props"] for _, p in graph.calls if "did" in p]
    chunk_props = next(p["chunks"] for _, p in graph.calls if "chunks" in p)
    assert all(json.loads(p["metadata_json"]) == {"area": "checkout"} for p in snapshot_props + chunk_props)
    assert all("metadata" not in p for p in snapshot_props + chunk_props)
    data["sources"][0]["metadata"] = {"area": "catalog"}
    path = tmp_path / "project.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    _, changed = pipeline.collect(path, tmp_path / "runs")
    assert {c.id for c in corpus.chunks}.isdisjoint(c.id for c in changed.chunks)
