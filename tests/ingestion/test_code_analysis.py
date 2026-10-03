"""Static source analysis from immutable Git revisions."""

import json
import subprocess

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tests.support.graph import ROOT, MemoryReader
from trace_impact import create_pipeline
from trace_impact.ingestion.code.config import CodeGraphConfig, TypeScriptOptions, code_schema
from trace_impact.retrieval.graph.service import ImpactRetriever
from trace_impact.shared.graph_models import ImpactQuery


def test_real_compiler_resolves_aliases_shadowing_and_ignores_dirty_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}, "jsx": "preserve"},
                "exclude": ["src/archive.ts"],
            }
        )
    )
    (repo / "src/archive.ts").write_text("export function excluded() { return 0; }\n")
    (repo / "src/a.ts").write_text("export function discount() { return 1; }\n")
    (repo / "src/b.tsx").write_text(
        "import {discount as apply} from '@/a';\n"
        "export function Summary() { return apply(); }\n"
        "export function unrelated() { const apply = () => 2; return apply(); }\n"
        "// discount() inside this comment is not a reference\n"
    )

    def git(*args):
        return (
            subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
            .stdout.decode()
            .strip()
        )

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    revision = git("rev-parse", "HEAD")
    (repo / "src/a.ts").write_text("INVALID DIRTY SOURCE")
    config = CodeGraphConfig(project_id="unit", repository_path="repo", revision=revision)
    config_path = tmp_path / "code.json"
    config_path.write_text(config.model_dump_json())
    with create_pipeline() as app:
        graph = app.analyze_code(config_path)
        again = app.analyze_code(config_path)
        assert graph == again
        schema = code_schema(app.components)
        assert schema == json.loads((ROOT / "schemas/ingestion/code-graph.schema.json").read_text())
        Draft202012Validator(schema).validate(json.loads(config.model_dump_json()))
        nodes = {n.name: n for n in graph.nodes if n.kind == "CodeSymbol" and n.name != "<module>"}
        assert "excluded" not in nodes
        pairs = {(e.source, e.target) for e in graph.edges if e.type == "DEPENDS_ON"}
        assert (nodes["Summary"].id, nodes["discount"].id) in pairs
        assert (nodes["unrelated"].id, nodes["discount"].id) not in pairs
        assert not any(n.kind in {"UIElement", "UserFlow"} for n in graph.nodes)
        query = {"changed_files": ["src/a.ts"], "scope": {"project_id": "unit", "revision": revision}}
        result = ImpactRetriever(MemoryReader(graph)).retrieve(query)
        assert set(result.code_files) == {"src/a.ts", "src/b.tsx"}
        assert result.status == "UNMAPPED"
        query["changed_files"].append("src/missing.ts")
        assert ImpactRetriever(MemoryReader(graph)).retrieve(query).status == "SYMBOL_NOT_FOUND"
        limited = config.model_copy(
            update={"analyzer": config.analyzer.model_copy(update={"options": {"max_files": 1}})}
        )
        config_path.write_text(limited.model_dump_json())
        with pytest.raises(ValueError, match="budget"):
            app.analyze_code(config_path)


def test_options_are_bounded():
    with pytest.raises(ValidationError):
        TypeScriptOptions(timeout_seconds=0)
    with pytest.raises(ValidationError):
        ImpactQuery(scope={"project_id": "p", "revision": "v", "max_dependency_hops": 100})
