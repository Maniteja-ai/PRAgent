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
from trace_impact.shared.errors import SourceReadError
from trace_impact.shared.graph_models import ImpactQuery
from trace_impact.shared.stage_config import StageConfig


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


def test_compiler_extracts_static_impact_ids_on_owning_component(tmp_path):
    repo, revision = typescript_repo(
        tmp_path,
        'export function Summary() { return <button data-impact-id="checkout.discount.apply">Apply</button>; }\n',
    )
    with create_pipeline() as app:
        graph = app.components.code_analyzers.resolve(StageConfig(provider="typescript")).analyze(
            CodeGraphConfig(project_id="unit", repository_path=str(repo), revision=revision)
        )
    summary = next(node for node in graph.nodes if node.name == "Summary")
    assert summary.properties["impact_ids"] == ["checkout.discount.apply"]
    assert any("Stable UI tags: 1" in diagnostic for diagnostic in graph.diagnostics)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            'const impactId = "checkout.discount.apply"; export function Summary() { return <button data-impact-id={impactId}>Apply</button>; }\n',
            "must be a static string",
        ),
        (
            'export function Summary() { return <><button data-impact-id="checkout.discount.apply">A</button><button data-impact-id="checkout.discount.apply">B</button></>; }\n',
            "duplicate data-impact-id",
        ),
    ],
)
def test_compiler_rejects_unprovable_impact_ids(tmp_path, source, message):
    repo, revision = typescript_repo(tmp_path, source)
    analyzer = create_pipeline()
    with analyzer as app, pytest.raises(SourceReadError, match=message):
        app.components.code_analyzers.resolve(StageConfig(provider="typescript")).analyze(
            CodeGraphConfig(project_id="unit", repository_path=str(repo), revision=revision)
        )


def typescript_repo(tmp_path, source):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"jsx": "preserve"}, "include": ["src/**/*"]})
    )
    (repo / "src/summary.tsx").write_text(source)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, check=True, text=True
    ).stdout.strip()
    return repo, revision
