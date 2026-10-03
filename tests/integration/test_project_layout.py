"""Offline integration checks for package boundaries and moved configuration paths."""

import ast
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def test_all_shipped_configuration_schema_links_resolve_and_validate():
    configs = sorted((ROOT / "configs").rglob("*.json"))
    assert len(configs) >= 15, "Configuration tree was not discovered"
    checked = 0
    for path in configs:
        value = json.loads(path.read_text(encoding="utf-8"))
        if "$schema" not in value:
            continue
        schema_path = (path.parent / value["$schema"]).resolve(strict=True)
        assert schema_path.is_relative_to(ROOT / "schemas")
        Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(value)
        checked += 1
    assert checked >= 10


def test_ingestion_has_no_direct_retrieval_or_evaluation_dependency():
    for path in (ROOT / "src/trace_impact/ingestion").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            assert not any(m.startswith(("trace_impact.retrieval", "trace_impact.evals")) for m in modules), (
                path
            )


def test_evaluation_config_input_paths_exist():
    required = {"dataset", "inputs", "labels", "baseline", "retrieval_config", "project"}
    for path in (ROOT / "configs/evals").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in required & data.keys():
            assert (path.parent / data[key]).resolve().exists(), (path, key)
