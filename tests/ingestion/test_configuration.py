"""Friendly configuration must not sacrifice plugin extensibility or cached-run identity."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from trace_impact import create_pipeline
from trace_impact.ingestion.config import ExtractionConfig, Project, load_project
from trace_impact.ingestion.schema import project_schema
from trace_impact.shared.errors import ConfigurationError
from trace_impact.shared.registry import Registry

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("spelling", ["web", "WEB", "Web", "  WEB  "])
def test_loader_spelling_resolves_to_same_component(spelling):
    project = load_project(ROOT / "configs/ingestion/saleor/project.json").model_dump()
    project["sources"][1]["loader"] = spelling
    parsed = Project.model_validate(project)
    assert parsed.sources[1].loader == "web"
    with create_pipeline() as app:
        assert app.components.loaders.resolve(spelling) is app.components.loaders.resolve("web")


def test_all_component_selections_normalize_but_source_and_model_ids_do_not():
    project = load_project(ROOT / "configs/ingestion/saleor/project.json").model_dump()
    project["sources"][0].update(loader=" GitHub_File ", parser="MARKDOWN", format="Markdown")
    project["sources"][0]["options"]["path"] = "Docs/README.md"
    project["sources"][0]["location"] = "https://Example.com/Docs/README.md"
    project.update(chunker="SECTION", storage={"graph": "Neo4j", "vector": "QDRANT", "artifacts": "Local"})
    project["extractor"].update(provider="Gemini", model="CaseSensitive-Model-ID")
    project["embedding_provider"]["provider"] = "GEMINI"
    parsed = Project.model_validate(project)
    assert parsed.sources[0].loader_name == "github_file"
    assert parsed.sources[0].parser_name == "markdown"
    assert parsed.sources[0].location == "https://Example.com/Docs/README.md"
    assert parsed.sources[0].options["path"] == "Docs/README.md"
    assert parsed.extractor.provider == parsed.embedding_provider.provider == "gemini"
    assert parsed.extractor.model == "CaseSensitive-Model-ID"
    assert parsed.chunker == "section"
    assert parsed.storage.model_dump() == {"graph": "neo4j", "vector": "qdrant", "artifacts": "local"}
    project.update(extractor=" LANGCHAIN ", embedding_provider="OpenAI")
    parsed = Project.model_validate(project)
    assert parsed.extractor == "langchain" and parsed.embedding_provider == "openai"


@pytest.mark.parametrize("bad", ["", " ", "web loader", "web.py", 42])
def test_invalid_component_names_rejected(bad):
    data = load_project(ROOT / "configs/ingestion/example/project.json").model_dump()
    data["sources"][0]["loader"] = bad
    with pytest.raises(ValidationError):
        Project.model_validate(data)


def test_custom_names_normalize_and_case_collisions_are_rejected():
    registry = Registry("loader")
    instance = object()
    registry.register(" My_Custom_Loader ", instance)
    assert registry.resolve("MY_CUSTOM_LOADER") is instance
    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register("my_custom_loader", object())
    assert registry.names() == ["my_custom_loader"]
    registry.close()


def test_configured_provider_case_uses_one_cached_instance():
    registry = Registry("extractor")
    seen = []

    def factory(config):
        seen.append(config.provider)
        return object()

    registry.register_configured_factory(" Custom ", factory)
    lower = ExtractionConfig(provider="custom", model="Exact-Model")
    upper = ExtractionConfig(provider="CUSTOM", model="Exact-Model")
    assert registry.resolve(lower) is registry.resolve(upper)
    assert seen == ["custom"]
    with pytest.raises(ConfigurationError, match="already registered"):
        registry.register_configured_factory("CUSTOM", factory)
    registry.close()


def test_typo_gives_suggestion_before_any_source_read(tmp_path):
    data = load_project(ROOT / "configs/ingestion/example/project.json").model_dump()
    data["sources"][0]["loader"] = "webb"
    path = tmp_path / "project.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with create_pipeline() as app:
        with pytest.raises(ConfigurationError, match="Did you mean 'web'"):
            app.collect(path, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_schema_reference_does_not_change_runtime_config_or_cache_identity():
    data = load_project(ROOT / "configs/ingestion/saleor/project.json").model_dump()
    original = Project.model_validate(data)
    data["$schema"] = "../../schemas/ingestion/project.schema.json"
    decorated = Project.model_validate(data)
    assert decorated.model_dump_json() == original.model_dump_json()
    assert "$schema" not in decorated.model_dump(by_alias=True)


def test_generated_schema_is_valid_current_and_accepts_extensions():
    with create_pipeline() as app:
        schema = project_schema(app.components)
    Draft202012Validator.check_schema(schema)
    assert json.loads((ROOT / "schemas/ingestion/project.schema.json").read_text(encoding="utf-8")) == schema
    validator = Draft202012Validator(schema)
    data = json.loads((ROOT / "configs/ingestion/saleor/project.json").read_text(encoding="utf-8"))
    validator.validate(data)
    data["sources"][0]["loader"] = "CUSTOM_LOADER"
    data["extractor"]["provider"] = "Custom_Provider"
    validator.validate(data)
    assert "web" in schema["$defs"]["Source"]["properties"]["loader"]["anyOf"][0]["enum"]
    assert "gemini" in schema["$defs"]["ExtractionConfig"]["properties"]["provider"]["anyOf"][0]["enum"]
    # Gemini requires an object with a model, not the legacy string form.
    assert "gemini" not in schema["properties"]["extractor"]["anyOf"][0]["enum"]


def test_registered_custom_components_can_be_added_to_editor_suggestions():
    with create_pipeline() as app:
        app.components.loaders.register_factory("Future_Source", lambda: pytest.fail("Must stay lazy"))
        schema = project_schema(app.components)
    assert "future_source" in schema["$defs"]["Source"]["properties"]["loader"]["anyOf"][0]["enum"]
