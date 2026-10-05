import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingestion import JsonConfigLoader, create_pipeline
from ingestion.beans.container import BeanContainer
from ingestion.beans.decorators import component
from ingestion.bootstrap import create_code_index
from ingestion.chunking_strategy.implementation.syntax_aware_code_chunker import SyntaxAwareCodeChunker
from ingestion.chunking_strategy.interface import ChunkingStrategy
from ingestion.config_loader.models import CodeInputConfig, InputConfig, UiTaggingConfig
from ingestion.domain.models import (
    CodeGraph,
    CodeIndexResult,
    ConfirmedMapping,
    GraphRecord,
    GraphRelationship,
    RawDocument,
    UiObservation,
    VectorRecord,
)
from ingestion.extractor.code.implementation.typescript_code_extractor import TypeScriptCodeExtractor
from ingestion.mapping.implementation.evidence_mapping import EvidenceMappingResolver
from ingestion.mapping.implementation.nextjs_route_mapping import NextJsRouteMappingResolver
from ingestion.mapping.implementation.static_dependency_ui_tagger import StaticDependencyUiTagger
from ingestion.storage.artifacts.implementation.local_artifact_store import LocalArtifactStore
from ingestion.storage.implementation.in_memory import InMemoryGraphStore, InMemoryVectorStore
from ingestion.storage.implementation.qdrant_vector_store import QdrantVectorStore


def test_static_ui_tagger_marks_import_reachability_as_unconfirmed_candidate():
    graph = CodeGraph(
        nodes=(
            GraphRecord(id="file:src/app/[channel]/(main)/cart/page.tsx", kind="CodeFile", properties={"path": "src/app/[channel]/(main)/cart/page.tsx"}),
            GraphRecord(id="file:src/components/cart-panel.tsx", kind="CodeFile", properties={"path": "src/components/cart-panel.tsx"}),
            GraphRecord(id="file:src/lib/format.ts", kind="CodeFile", properties={"path": "src/lib/format.ts"}),
        ),
        relationships=(
            GraphRelationship(id="page-panel", source_id="file:src/app/[channel]/(main)/cart/page.tsx", target_id="file:src/components/cart-panel.tsx", kind="IMPORTS"),
            GraphRelationship(id="panel-format", source_id="file:src/components/cart-panel.tsx", target_id="file:src/lib/format.ts", kind="IMPORTS"),
        ),
        source_documents=tuple(
            RawDocument(source_id=f"code:{path}", content="source", media_type="text/plain", metadata={"kind": "code", "code_file_id": f"file:{path}", "path": path})
            for path in ("src/app/[channel]/(main)/cart/page.tsx", "src/components/cart-panel.tsx", "src/lib/format.ts")
        ),
    )

    tagged = StaticDependencyUiTagger().tag(graph, UiTaggingConfig(max_dependency_hops=2))
    metadata_by_path = {document.metadata["path"]: document.metadata for document in tagged.source_documents}

    assert metadata_by_path["src/app/[channel]/(main)/cart/page.tsx"]["ui_route_candidates"] == ("/{channel}/cart",)
    assert metadata_by_path["src/components/cart-panel.tsx"]["ui_route_candidates"] == ("/{channel}/cart",)
    assert metadata_by_path["src/lib/format.ts"]["ui_mapping_status"] == "candidate"
    assert metadata_by_path["src/lib/format.ts"]["ui_mapping_basis"] == "static_import_reachability"


def write_config(root: Path, document: Path, repository: Path, revision: str) -> Path:
    manifest = {
        "schema_version": 1,
        "project": {"id": "demo", "name": "Demo"},
        "files": {
            "input": "input.json",
            "models": "models.json",
            "chunking": "chunking.json",
            "storage": "storage.json",
            "constraints": "constraints.json",
            "evaluation": "evaluation.json",
        },
    }
    sections = {
        "input": {
            "repository": {"url": "https://example.invalid/repo", "baseline_commit": revision},
            "baseline_url": "https://example.invalid",
            "scope": ["demo"],
            "documents": {
                "sources": [{"id": "spec", "location": str(document), "loader": "local_file"}]
            },
            "code": {
                "enabled": True,
                "repository_path": str(repository),
                "revision": revision,
                "analyzer": {"provider": "git"},
            },
            "ui": {
                "enabled": True,
                "base_url": "https://example.invalid",
                "seed_paths": ["/"],
                "explorer": {"provider": "recorded"},
                "mapping_resolver": {"provider": "none"},
            },
        },
        "models": {
            "requirement_extraction": {
                "enabled": False,
                "implementation": "none",
                "max_chunks": 1,
            },
            "embedding": {
                "enabled": True,
                "implementation": "deterministic",
                "batch_size": 2,
            },
            "evaluation_judge": {"enabled": False},
        },
        "chunking": {"provider": "section", "max_chunk_chars": 100},
        "storage": {
            "artifacts": {"provider": "local", "run_directory": "runs"},
            "graph": {"provider": "memory"},
            "vector": {"provider": "memory"},
        },
        "constraints": {},
        "evaluation": {
            "recording": {
                "enabled": True,
                "provider": "jsonl",
                "directory": "evaluation-results",
            },
            "metrics": {},
        },
    }
    for name, section in sections.items():
        (root / f"{name}.json").write_text(json.dumps(section), encoding="utf-8")
    (root / "ingestion.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root / "ingestion.json"


def test_configuration_beans_are_injected_into_working_pipeline(tmp_path, monkeypatch):
    document = tmp_path / "spec.md"
    document.write_text(
        "# Cart\n\nA shopper can add a product.\n\nCheckout shows a total.", encoding="utf-8"
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "view.tsx").write_text("export const View = () => null", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
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
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    config = JsonConfigLoader().load(write_config(tmp_path, document, repository, revision))
    result = create_pipeline(config).run()
    assert result.documents == 1
    assert result.chunks == 4
    assert result.vectors == 4
    assert result.code_files == 1
    assert result.code_chunks == 1
    assert result.graph_records == 1
    assert result.graph_relationships == 0
    assert result.ui_observations == 1
    assert result.confirmed_mappings == 0
    assert result.requirements == 0
    artifact_directory = tmp_path / "runs" / "demo" / revision
    assert {path.name for path in artifact_directory.iterdir()} == {
        "chunks.json",
        "code_chunks.json",
        "code_documents.json",
        "config.json",
        "confirmed_mappings.json",
        "documents.json",
        "graph_records.json",
        "graph_relationships.json",
        "result.json",
        "requirements.json",
        "ui_observations.json",
        "vectors.json",
    }
    code_documents = json.loads((artifact_directory / "code_documents.json").read_text())
    assert code_documents[0]["metadata"]["code_file_id"] == "file:view.tsx"
    assert code_documents[0]["metadata"]["revision"] == revision
    code_chunks = json.loads((artifact_directory / "code_chunks.json").read_text())
    assert code_chunks[0]["metadata"]["code_file_id"] == "file:view.tsx"
    observations = [
        json.loads(line)
        for line in (tmp_path / "evaluation-results" / "stage-observations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {observation["stage"] for observation in observations} == {
        "document_extraction",
        "chunking",
        "code_chunking",
        "embedding_and_vector_storage",
        "code_graph_storage",
        "requirement_extraction",
        "code_graph_extraction",
        "graph_storage",
        "ui_exploration",
        "mapping_resolution",
        "finalization",
        "ingestion_pipeline",
    }
    assert all(observation["status"] == "COMPLETED" for observation in observations)

    (artifact_directory / "failure.json").write_text(
        json.dumps({"error_type": "TestFailure", "message": "simulated interruption"}),
        encoding="utf-8",
    )
    with create_pipeline(config) as resumed_pipeline:
        monkeypatch.setattr(
            resumed_pipeline.embedding_provider,
            "embed",
            lambda *_args: pytest.fail("resume must not recompute embeddings"),
        )
        resumed_result = resumed_pipeline.resume_after_embedding()

    assert resumed_result.vectors == result.vectors
    assert resumed_result.chunks == result.chunks
    assert not (artifact_directory / "failure.json").exists()
    archived_failures = tuple((artifact_directory / "failure_history").glob("failure-*.json"))
    assert len(archived_failures) == 1
    assert json.loads(archived_failures[0].read_text(encoding="utf-8"))["error_type"] == "TestFailure"


def test_container_rejects_duplicate_and_unknown_beans():
    @component(contract=ChunkingStrategy, name="same")
    class First:
        pass

    @component(contract=ChunkingStrategy, name="same")
    class Second:
        pass

    container = BeanContainer()
    with pytest.raises(ValueError, match="Duplicate component"):
        container.register_components((First, Second))
    with pytest.raises(ValueError, match="Unknown ChunkingStrategy"):
        BeanContainer().select(ChunkingStrategy, "missing")


def test_graph_store_keeps_only_explicit_confirmed_mappings():
    mapping = ConfirmedMapping(
        id="mapping:file:view.tsx:page:checkout",
        source_id="file:view.tsx",
        target_id="page:checkout",
        confidence=0.98,
        evidence_ids=("browser-run:42",),
    )
    store = InMemoryGraphStore()
    assert store.save_mappings((mapping,)) == 1
    assert store.mappings[mapping.id] == mapping

    with pytest.raises(ValueError):
        ConfirmedMapping(
            id="invalid",
            source_id="file:view.tsx",
            target_id="page:checkout",
            confidence=0.98,
            evidence_ids=(),
        )


def test_graph_store_replaces_code_graph_without_removing_other_records():
    store = InMemoryGraphStore()
    store.save(
        (
            GraphRecord(id="file:old.ts", kind="CodeFile"),
            GraphRecord(id="symbol:old", kind="CodeSymbol"),
            GraphRecord(id="requirement:1", kind="Requirement"),
        )
    )
    store.save_relationships(
        (
            GraphRelationship(
                id="old-edge",
                source_id="file:old.ts",
                target_id="symbol:old",
                kind="DECLARES",
            ),
        )
    )

    store.replace_code_graph((GraphRecord(id="file:new.ts", kind="CodeFile"),), ())

    assert set(store.records) == {"file:new.ts", "requirement:1"}
    assert not store.relationships


def test_vector_store_replaces_code_for_the_active_revision_only():
    store = InMemoryVectorStore()
    document = VectorRecord(id="document", vector=(0.1,), content="docs")
    old_code = VectorRecord(
        id="old-code", vector=(0.2,), content="old", metadata={"kind": "code"}
    )
    new_code = VectorRecord(
        id="new-code", vector=(0.3,), content="new", metadata={"kind": "code"}
    )
    store.save((document, old_code))

    store.replace_code_records((new_code,))

    assert set(store.records) == {"document", "new-code"}


def test_qdrant_code_refresh_removes_old_revision_without_removing_documents(tmp_path):
    document = tmp_path / "spec.md"
    document.write_text("# Cart", encoding="utf-8")
    repository = tmp_path / "repo"
    repository.mkdir()
    old_revision = "a" * 40
    config_path = write_config(tmp_path, document, repository, old_revision)
    storage_path = tmp_path / "qdrant"
    storage_path.mkdir()
    storage_data = json.loads((tmp_path / "storage.json").read_text(encoding="utf-8"))
    storage_data["vector"] = {
        "provider": "qdrant",
        "connection": {
            "path": str(storage_path),
            "collection": "code-refresh-test",
            "similarity": "cosine",
        },
    }
    (tmp_path / "storage.json").write_text(json.dumps(storage_data), encoding="utf-8")
    old_config = JsonConfigLoader().load(config_path)
    old_store = QdrantVectorStore(old_config, old_config.storage)
    document_vector = VectorRecord(
        id="documentation", vector=(0.1, 0.2, 0.3), content="docs", metadata={"kind": "doc"}
    )
    old_code = VectorRecord(
        id="old-code",
        vector=(0.3, 0.2, 0.1),
        content="old source",
        metadata={"kind": "code", "code_file_id": "file:old.ts"},
    )
    old_store.save((document_vector, old_code))
    old_store.close()

    new_revision = "b" * 40
    new_input = old_config.input.model_copy(
        update={
            "repository": old_config.input.repository.model_copy(
                update={"baseline_commit": new_revision}
            )
        }
    )
    new_config = old_config.model_copy(update={"input": new_input})
    new_store = QdrantVectorStore(new_config, new_config.storage)
    new_code = VectorRecord(
        id="new-code",
        vector=(0.4, 0.5, 0.6),
        content="new source",
        metadata={
            "kind": "code",
            "code_file_id": "file:new.ts",
            "ui_route_candidates": ("/checkout",),
            "ui_tags": ("checkout", "voucher"),
            "ui_mapping_status": "candidate",
        },
    )
    new_store.replace_code_records((new_code,))
    try:
        assert new_store._client.count("code-refresh-test", exact=True).count == 2
        points, _ = new_store._client.scroll("code-refresh-test", with_payload=True, limit=10)
    finally:
        new_store.close()

    payloads = [point.payload for point in points]
    assert any(payload["record_id"] == "documentation" for payload in payloads)
    assert any(
        payload["record_id"] == "new-code" and payload["revision"] == new_revision
        for payload in payloads
    )
    tagged_payload = next(payload for payload in payloads if payload["record_id"] == "new-code")
    assert tagged_payload["metadata"]["ui_route_candidates"] == ["/checkout"]
    assert not any(payload["record_id"] == "old-code" for payload in payloads)


def test_evidence_mapping_rejects_unknown_code_and_accepts_known_code():
    resolver = EvidenceMappingResolver()
    observation = UiObservation(
        id="page:checkout",
        url="https://example.invalid/checkout",
        confirmed_code_ids=("file:src/checkout.tsx",),
        evidence_ids=("browser:checkout",),
    )
    with pytest.raises(ValueError, match="unknown code IDs"):
        resolver.resolve((), (observation,))
    mappings = resolver.resolve(
        (GraphRecord(id="file:src/checkout.tsx", kind="CodeFile"),),
        (observation,),
    )
    assert len(mappings) == 1
    assert mappings[0].target_id == "page:checkout"


def test_typescript_extractor_creates_import_relationship(tmp_path):
    repository = tmp_path / "typescript-repo"
    source = repository / "src"
    source.mkdir(parents=True)
    (source / "cart.ts").write_text("export const cart = true;", encoding="utf-8")
    (source / "checkout.ts").write_text(
        'import { cart } from "./cart";\nexport const checkout = cart;', encoding="utf-8"
    )
    (repository / "tsconfig.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
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
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    input_config = InputConfig.model_validate(
        {
            "repository": {"url": "https://example.invalid/repo", "baseline_commit": revision},
            "baseline_url": "https://example.invalid",
            "scope": ["demo"],
            "documents": {"sources": []},
            "code": {
                "repository_path": str(repository),
                "revision": revision,
                "analyzer": {"provider": "typescript", "options": {"source_prefixes": ["src/"]}},
            },
        }
    )
    graph = TypeScriptCodeExtractor(input_config, StaticDependencyUiTagger()).extract(
        CodeInputConfig(repository_path=repository, revision=revision)
    )
    assert len(graph.nodes) == 2
    assert len(graph.relationships) == 1
    assert graph.relationships[0].kind == "IMPORTS"


def test_typescript_extractor_indexes_graphql_styles_and_root_config_for_rag(tmp_path):
    repository = tmp_path / "source-repo"
    source = repository / "src"
    source.mkdir(parents=True)
    (source / "checkout.tsx").write_text(
        'import "./checkout.css";\nimport query from "./checkout-query";\n'
        "export function Checkout() { return query; }\n",
        encoding="utf-8",
    )
    (source / "checkout-query.graphql").write_text(
        "query CheckoutQuery { checkout { id } }\n", encoding="utf-8"
    )
    (source / "checkout.css").write_text(".checkout { display: grid; }\n", encoding="utf-8")
    (repository / "package.json").write_text('{"name":"storefront"}', encoding="utf-8")
    (repository / "tsconfig.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
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
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    input_config = InputConfig.model_validate(
        {
            "repository": {"url": "https://example.invalid/repo", "baseline_commit": revision},
            "baseline_url": "https://example.invalid",
            "scope": ["checkout"],
            "documents": {"sources": []},
            "code": {
                "repository_path": str(repository),
                "revision": revision,
                "analyzer": {
                    "provider": "typescript",
                    "options": {"source_prefixes": ["src/", "package.json"]},
                },
            },
        }
    )

    graph = TypeScriptCodeExtractor(input_config, StaticDependencyUiTagger()).extract(
        CodeInputConfig(repository_path=repository, revision=revision)
    )

    file_ids = {item.id for item in graph.nodes if item.kind == "CodeFile"}
    assert file_ids == {
        "file:src/checkout.tsx",
        "file:src/checkout-query.graphql",
        "file:src/checkout.css",
        "file:package.json",
    }
    assert {item.metadata["code_file_id"] for item in graph.source_documents} == file_ids
    assert {
        (item.source_id, item.target_id, item.kind) for item in graph.relationships
    } >= {
        ("file:src/checkout.tsx", "file:src/checkout-query.graphql", "IMPORTS"),
        ("file:src/checkout.tsx", "file:src/checkout.css", "IMPORTS"),
    }


def test_typescript_extractor_resolves_tsconfig_path_aliases(tmp_path):
    repository = tmp_path / "alias-repo"
    app = repository / "src" / "app" / "checkout"
    feature = repository / "src" / "checkout"
    app.mkdir(parents=True)
    feature.mkdir(parents=True)
    (repository / "tsconfig.json").write_text(
        '{"compilerOptions":{"paths":{"@/*":["./src/*"]}}}', encoding="utf-8"
    )
    (app / "page.tsx").write_text(
        'const checkout = import("@/checkout"); export default checkout;', encoding="utf-8"
    )
    (feature / "index.tsx").write_text("export const Checkout = () => null;", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
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
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    input_config = InputConfig.model_validate(
        {
            "repository": {"url": "https://example.invalid/repo", "baseline_commit": revision},
            "baseline_url": "https://example.invalid",
            "scope": ["demo"],
            "documents": {"sources": []},
            "code": {"repository_path": str(repository), "revision": revision},
        }
    )

    graph = TypeScriptCodeExtractor(input_config, StaticDependencyUiTagger()).extract(
        CodeInputConfig(repository_path=repository, revision=revision)
    )

    imports = [item for item in graph.relationships if item.kind == "IMPORTS"]
    assert [(item.source_id, item.target_id) for item in imports] == [
        ("file:src/app/checkout/page.tsx", "file:src/checkout/index.tsx")
    ]


def test_typescript_extractor_builds_function_and_component_dependency_graph(tmp_path):
    repository = tmp_path / "symbol-repo"
    source = repository / "src"
    source.mkdir(parents=True)
    (repository / "tsconfig.json").write_text("{}", encoding="utf-8")
    (source / "promo.tsx").write_text(
        "export function applyPromoCode() { refreshCheckout(); }\n"
        "function refreshCheckout() { validatePromotion(); }\n"
        "function validatePromotion() {}\n"
        "export const PromoBadge = () => <span />;\n",
        encoding="utf-8",
    )
    (source / "checkout.tsx").write_text(
        'import { applyPromoCode, PromoBadge } from "./promo";\n'
        "export function CheckoutPage() { applyPromoCode(); return <PromoBadge />; }\n",
        encoding="utf-8",
    )
    (source / "hierarchy.ts").write_text(
        "export interface Contract {}\n"
        "export class Base {}\n"
        "export class Child extends Base implements Contract {}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
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
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    input_config = InputConfig.model_validate(
        {
            "repository": {"url": "https://example.invalid/repo", "baseline_commit": revision},
            "baseline_url": "https://example.invalid",
            "scope": ["demo"],
            "documents": {"sources": []},
            "code": {
                "repository_path": str(repository),
                "revision": revision,
                "analyzer": {"provider": "typescript", "options": {"source_prefixes": ["src/"]}},
            },
        }
    )

    graph = TypeScriptCodeExtractor(input_config, StaticDependencyUiTagger()).extract(
        CodeInputConfig(repository_path=repository, revision=revision)
    )

    symbols = {item.id: item for item in graph.nodes if item.kind == "CodeSymbol"}
    checkout_page = next(
        item for item in symbols.values() if item.properties["name"] == "CheckoutPage"
    )
    apply_promo = next(
        item for item in symbols.values() if item.properties["name"] == "applyPromoCode"
    )
    promo_badge = next(
        item for item in symbols.values() if item.properties["name"] == "PromoBadge"
    )
    relations = {
        (item.source_id, item.target_id, item.kind) for item in graph.relationships
    }
    assert (checkout_page.id, apply_promo.id, "CALLS") in relations
    refresh_checkout = next(
        item for item in symbols.values() if item.properties["name"] == "refreshCheckout"
    )
    validate_promotion = next(
        item for item in symbols.values() if item.properties["name"] == "validatePromotion"
    )
    assert (apply_promo.id, refresh_checkout.id, "CALLS") in relations
    assert (refresh_checkout.id, validate_promotion.id, "CALLS") in relations
    base = next(item for item in symbols.values() if item.properties["name"] == "Base")
    child = next(item for item in symbols.values() if item.properties["name"] == "Child")
    contract = next(item for item in symbols.values() if item.properties["name"] == "Contract")
    assert (child.id, base.id, "EXTENDS") in relations
    assert (child.id, contract.id, "IMPLEMENTS") in relations
    assert (checkout_page.id, promo_badge.id, "RENDERS") in relations
    assert (
        "file:src/checkout.tsx",
        "file:src/promo.tsx",
        "CALLS",
    ) in relations
    assert (
        "file:src/checkout.tsx",
        "file:src/promo.tsx",
        "RENDERS",
    ) in relations


def test_syntax_aware_chunker_keeps_functions_separate_and_adds_symbol_metadata():
    document = RawDocument(
        source_id="code:src/chain.ts",
        media_type="text/typescript",
        content=(
            "export function func1() { func2(); }\n"
            "export function func2() { func3(); }\n"
            "export function func3() { return true; }\n"
        ),
        metadata={
            "kind": "code",
            "path": "src/chain.ts",
            "language": "typescript",
            "ui_route_candidates": ("/cart",),
        },
    )

    chunks = SyntaxAwareCodeChunker().chunk(document, max_chars=1000)

    by_name = {chunk.metadata["symbol_name"]: chunk for chunk in chunks}
    assert set(by_name) == {"func1", "func2", "func3"}
    assert by_name["func1"].content == "export function func1() { func2(); }"
    assert by_name["func1"].metadata["symbol_kind"] == "Function"
    assert by_name["func1"].metadata["called_symbols"] == ("CALLS:func2",)
    assert by_name["func1"].metadata["ui_route_candidates"] == ("/cart",)
    assert by_name["func1"].metadata["start_line"] == 1
    assert by_name["func3"].metadata["start_line"] == 3


def test_code_index_pipeline_writes_vectors_and_replaces_dependency_graph(tmp_path):
    document = tmp_path / "spec.md"
    document.write_text("# Checkout", encoding="utf-8")
    repository = tmp_path / "repo"
    source = repository / "src"
    source.mkdir(parents=True)
    (source / "discount.ts").write_text(
        "export function applyDiscount() { return true; }\n", encoding="utf-8"
    )
    (source / "checkout.tsx").write_text(
        'import { applyDiscount } from "./discount";\n'
        "export function Checkout() { applyDiscount(); return null; }\n",
        encoding="utf-8",
    )
    (repository / "tsconfig.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
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
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    config_path = write_config(tmp_path, document, repository, revision)
    input_path = tmp_path / "input.json"
    input_data = json.loads(input_path.read_text(encoding="utf-8"))
    input_data["code"]["analyzer"] = {
        "provider": "typescript",
        "options": {"source_prefixes": ["src/"]},
    }
    input_path.write_text(json.dumps(input_data), encoding="utf-8")
    config = JsonConfigLoader().load(config_path)

    pipeline = create_code_index(config)
    result = pipeline.run()

    assert result.revision == revision
    assert result.code_files == 2
    assert result.code_symbols == 2
    assert result.chunks == result.vectors > 0
    assert result.vector_dimensions == 8
    assert result.relationships > 0
    assert any(item.kind == "CALLS" for item in pipeline.graph_store.relationships.values())
    assert len(pipeline.vector_store.records) == result.vectors
    assert (tmp_path / "runs" / "demo" / revision / "code_index.json").exists()
    pipeline.close()


def test_resume_vector_count_includes_code_index_without_double_counting(tmp_path):
    document = tmp_path / "spec.md"
    document.write_text("# Cart", encoding="utf-8")
    repository = tmp_path / "repo"
    repository.mkdir()
    config = JsonConfigLoader().load(
        write_config(tmp_path, document, repository, "a" * 40)
    )
    store = LocalArtifactStore(config, config.storage)
    document_vector = VectorRecord(id="document", vector=(0.1,), content="doc")
    store.save_vectors((document_vector,))
    store.save_code_index(
        CodeIndexResult(
            run_id="code-run",
            revision="a" * 40,
            code_files=1,
            code_symbols=0,
            chunks=2,
            vectors=2,
            vector_dimensions=3072,
            relationships=0,
            completed_at=datetime.now(UTC),
        ),
        CodeGraph(),
        (),
    )

    assert store.load_vector_count() == 3

    store.save_vectors(
        (
            document_vector,
            VectorRecord(
                id="code",
                vector=(0.1,),
                content="source",
                metadata={"kind": "code"},
            ),
        )
    )
    assert store.load_vector_count() == 2


def test_nextjs_route_mapping_uses_framework_route_evidence():
    code = GraphRecord(
        id="file:src/app/[channel]/(main)/page.tsx",
        kind="CodeFile",
        properties={"path": "src/app/[channel]/(main)/page.tsx", "sha256": "source-hash"},
    )
    observation = UiObservation(
        id="page:0",
        url="https://shop.example/default-channel",
        evidence_ids=("browser:page-hash",),
        content_sha256="page-hash",
        page_title="Shop",
        visible_text="Products and collections",
        http_status=200,
        captured=True,
        content_ready=True,
    )
    mappings = NextJsRouteMappingResolver().resolve((code,), (observation,))
    assert len(mappings) == 1
    assert mappings[0].source_id == code.id
    assert mappings[0].basis == "framework_route"
    assert set(mappings[0].evidence_ids) == {"browser:page-hash", "code-sha256:source-hash"}


def test_nextjs_route_mapping_rejects_unready_page_without_evidence():
    code = GraphRecord(
        id="file:src/app/checkout/page.tsx",
        kind="CodeFile",
        properties={"path": "src/app/checkout/page.tsx", "sha256": "source-hash"},
    )
    loading_page = UiObservation(
        id="page:checkout", url="https://shop.example/checkout", visible_text="Loading..."
    )

    assert NextJsRouteMappingResolver().resolve((code,), (loading_page,)) == ()


def test_nextjs_route_mapping_can_confirm_page_route_without_claiming_loaded_controls():
    code = GraphRecord(
        id="file:src/app/checkout/page.tsx",
        kind="CodeFile",
        properties={"path": "src/app/checkout/page.tsx", "sha256": "source-hash"},
    )
    loading_page = UiObservation(
        id="page:checkout",
        url="https://shop.example/checkout",
        evidence_ids=("browser:captured-spinner",),
        page_title="Checkout",
        visible_text="Loading...",
        content_sha256="captured-spinner",
        http_status=200,
        captured=True,
        content_ready=False,
    )

    mappings = NextJsRouteMappingResolver().resolve((code,), (loading_page,))

    assert len(mappings) == 1
    assert mappings[0].basis == "framework_route"


def test_nextjs_route_mapping_prefers_static_route_over_dynamic_channel_route():
    dynamic_channel_route = GraphRecord(
        id="file:src/app/[channel]/(main)/page.tsx",
        kind="CodeFile",
        properties={
            "path": "src/app/[channel]/(main)/page.tsx",
            "sha256": "channel-hash",
        },
    )
    static_checkout_route = GraphRecord(
        id="file:src/app/checkout/page.tsx",
        kind="CodeFile",
        properties={"path": "src/app/checkout/page.tsx", "sha256": "checkout-hash"},
    )
    observation = UiObservation(
        id="page:checkout",
        url="https://shop.example/checkout",
        evidence_ids=("browser:checkout-page",),
        content_sha256="checkout-page",
        page_title="Checkout",
        visible_text="Loading...",
        http_status=200,
        captured=True,
    )

    mappings = NextJsRouteMappingResolver().resolve(
        (dynamic_channel_route, static_checkout_route), (observation,)
    )

    assert [mapping.source_id for mapping in mappings] == [static_checkout_route.id]
