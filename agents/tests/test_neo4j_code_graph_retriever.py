import hashlib
from pathlib import Path

import pytest

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.domain.models import ChangedFile, PullRequestRef, PullRequestSnapshot
from impact_agent.domain.pipeline_enums import CoverageGap
from impact_agent.tools.knowledge.implementations.neo4j_code_graph_retriever import (
    Neo4jCodeGraphRetriever,
    Neo4jCodeGraphRetrieverFactory,
    Neo4jGraphRetrievalError,
)


class FakeRecord(dict):
    def data(self):
        return self


class FakeTransaction:
    def __init__(self, driver):
        self.driver = driver

    def run(self, query, parameters):
        self.driver.calls.append((query, parameters))
        if "nodeTypeProperties" in query:
            return [FakeRecord(nodeLabels=["CodeFile"], propertyName="path")]
        if "relTypeProperties" in query:
            return [FakeRecord(relType="IMPORTS", propertyName="kind")]
        return self.driver.records

    def commit(self):
        self.driver.commits += 1

    def rollback(self):
        self.driver.rollbacks += 1


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def begin_transaction(self, **_kwargs):
        return FakeTransaction(self.driver)


class FakeDriver:
    def __init__(self, records):
        self.records = records
        self.calls = []
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def session(self, **_kwargs):
        return FakeSession(self)

    def close(self):
        self.closed = True


class FakePlanner:
    def __init__(self, query=None):
        self.query = query or (
            "MATCH (changed:CodeFile) "
            "WHERE changed.path IN $changed_paths AND changed.revision = $revision "
            "RETURN changed.path AS changed_path, [] AS related_files, "
            "[] AS related_symbols, [] AS confirmed_ui_mappings LIMIT 100"
        )
        self.schema = ""
        self.pull_request = None
        self.maximum_rows = None
        self.maximum_call_depth = None
        self.closed = False

    def create_query(
        self, pull_request, schema, indexed_revision, maximum_rows, maximum_call_depth
    ):
        assert indexed_revision == "revision"
        self.pull_request = pull_request
        self.schema = schema
        self.maximum_rows = maximum_rows
        self.maximum_call_depth = maximum_call_depth
        return self.query

    def close(self):
        self.closed = True


def _pull_request():
    return PullRequestSnapshot(
        PullRequestRef("owner/storefront", 42),
        "Update cart",
        "",
        "base",
        "head",
        (ChangedFile("src/cart.ts", "modified", 1, 0),),
        "diff",
    )


def test_graph_retriever_uses_schema_aware_plan_and_returns_confirmed_ui_evidence():
    driver = FakeDriver(
        [
            FakeRecord(
                changed_path="src/cart.ts",
                related_files=[{"path": "src/checkout.ts", "relationship_kinds": ["IMPORTS"]}],
                related_symbols=[
                    {
                        "name": "applyDiscount",
                        "symbol_kind": "Function",
                        "path": "src/checkout.ts",
                        "start_line": 4,
                        "end_line": 8,
                        "relationship_kinds": ["CALLS"],
                    }
                ],
                confirmed_ui_mappings=[
                    {
                        "code_path": "src/cart.ts",
                        "url": "https://store.example/cart",
                        "title": "Shopping Cart",
                        "basis": "framework_route",
                        "confidence": 1.0,
                        "evidence_ids": ["verified-ui-1", "code-sha256:route"],
                        "query_parameter_names": ["checkout", ""],
                    }
                ],
            )
        ]
    )
    planner = FakePlanner()
    retriever = Neo4jCodeGraphRetriever(driver, "saleor", "revision", 12, planner)

    evidence = retriever.retrieve(_pull_request())

    assert len(evidence) == 1
    assert "src/checkout.ts via IMPORTS" in evidence[0].content
    assert "Function applyDiscount (src/checkout.ts:4-8) via CALLS" in evidence[0].content
    assert "https://store.example/cart" in evidence[0].content
    assert "src/cart.ts" in evidence[0].content
    assert "route mapping identifies the page, not individual controls" in evidence[0].content
    assert "2 evidence references" in evidence[0].content
    assert len(evidence[0].confirmed_code_ui_mappings) == 1
    assert evidence[0].confirmed_code_ui_mappings[0].changed_path == "src/cart.ts"
    assert evidence[0].confirmed_code_ui_mappings[0].url == "https://store.example/cart"
    assert evidence[0].confirmed_code_ui_mappings[0].query_parameter_names == ("checkout",)
    assert "query parameters: checkout" in evidence[0].content
    assert evidence[0].content_sha256 == hashlib.sha256(evidence[0].content.encode()).hexdigest()
    assert '"CodeFile"' in planner.schema
    assert '"IMPORTS"' in planner.schema
    assert planner.pull_request.title == "Update cart"
    assert planner.maximum_rows == 100
    assert planner.maximum_call_depth == 3
    assert driver.calls[-1][1] == {"changed_paths": ["src/cart.ts"], "revision": "revision"}
    assert all(
        "revision" not in query or "changed_paths" in parameters
        for query, parameters in driver.calls
    )
    retriever.close()
    assert driver.closed and planner.closed


def test_graph_retriever_does_not_assign_related_page_mapping_to_changed_component():
    driver = FakeDriver(
        [
            FakeRecord(
                changed_path="src/cart.ts",
                related_files=[
                    {"path": "src/app/cart/page.tsx", "relationship_kinds": ["IMPORTS"]}
                ],
                related_symbols=[],
                confirmed_ui_mappings=[
                    {
                        "code_path": "src/app/cart/page.tsx",
                        "url": "https://store.example/cart",
                        "title": "Shopping Cart",
                        "basis": "framework_route",
                        "confidence": 1.0,
                        "evidence_ids": ["browser:cart", "code:page"],
                    }
                ],
            )
        ]
    )
    retriever = Neo4jCodeGraphRetriever(driver, "saleor", "revision", 12, FakePlanner())

    evidence = retriever.retrieve(_pull_request())

    assert evidence[0].confirmed_code_ui_mappings == ()
    assert "No confirmed UI mapping returned" in evidence[0].content
    assert evidence[1].coverage_gap is CoverageGap.CODE_TO_UI_MAPPING_MISSING


def test_graph_factory_fails_when_connection_secrets_are_missing():
    settings = JsonConfigLoader().load(Path(__file__).parents[1] / "config" / "default")

    with pytest.raises(ValueError, match="NEO4J_URI"):
        Neo4jCodeGraphRetrieverFactory.create(settings.graph_database, 12, {}, settings.models, 30)


def test_graph_retriever_records_missing_indexed_code_as_coverage_gap():
    retriever = Neo4jCodeGraphRetriever(FakeDriver([]), "saleor", "revision", 12, FakePlanner())

    evidence = retriever.retrieve(_pull_request())

    assert len(evidence) == 1
    assert evidence[0].coverage_gap is CoverageGap.CODE_GRAPH_DATA_MISSING
    assert "not evidence that the files have no UI impact" in evidence[0].content


def test_graph_retriever_records_missing_ui_mapping_as_coverage_gap():
    driver = FakeDriver(
        [
            FakeRecord(
                changed_path="src/cart.ts",
                related_files=[],
                related_symbols=[],
                confirmed_ui_mappings=[],
            )
        ]
    )
    retriever = Neo4jCodeGraphRetriever(driver, "saleor", "revision", 12, FakePlanner())

    evidence = retriever.retrieve(_pull_request())

    assert len(evidence) == 2
    assert evidence[0].coverage_gap is None
    assert evidence[1].coverage_gap is CoverageGap.CODE_TO_UI_MAPPING_MISSING
    assert "not evidence that the files have no UI impact" in evidence[1].content


def test_graph_retriever_merges_duplicate_rows_for_each_changed_file():
    driver = FakeDriver(
        [
            FakeRecord(
                changed_path="src/cart.ts",
                related_files=[{"path": "src/cart-actions.ts", "relationship_kinds": ["CALLS"]}],
                related_symbols=[],
                confirmed_ui_mappings=[],
            ),
            FakeRecord(
                changed_path="src/cart.ts",
                related_files=[{"path": "src/cart-page.tsx", "relationship_kinds": ["IMPORTS"]}],
                related_symbols=[],
                confirmed_ui_mappings=[],
            ),
        ]
    )
    retriever = Neo4jCodeGraphRetriever(driver, "saleor", "revision", 12, FakePlanner())

    evidence = retriever.retrieve(_pull_request())

    assert len(evidence) == 2
    assert "src/cart-actions.ts" in evidence[0].content
    assert "src/cart-page.tsx" in evidence[0].content
    assert evidence[1].coverage_gap is CoverageGap.CODE_TO_UI_MAPPING_MISSING


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) CREATE (x) RETURN n.path AS changed_path, [] AS related_files, "
        "[] AS related_symbols, [] AS confirmed_ui_mappings LIMIT 2",
        "CALL db.labels() YIELD label RETURN label AS changed_path, [] AS related_files, "
        "[] AS related_symbols, [] AS confirmed_ui_mappings LIMIT 2",
        "MATCH (n) RETURN n.path AS changed_path, [] AS related_files, "
        "[] AS related_symbols, [] AS confirmed_ui_mappings LIMIT 1001",
        "MATCH (n) RETURN n.path AS changed_path, [] AS related_files, "
        "[] AS related_symbols, [] AS confirmed_ui_mappings LIMIT 2; MATCH (m) RETURN m",
        "MATCH (n) WHERE n.path IN $changed_paths AND n.revision = $revision "
        "MATCH p=(n)-[:CODE_RELATIONSHIP*]->(m) RETURN n.path AS changed_path, "
        "[] AS related_files, [] AS related_symbols, [] AS confirmed_ui_mappings LIMIT 2",
        "MATCH (n) WHERE n.path IN $changed_paths AND n.revision = $revision "
        "MATCH p=(n)-[:CODE_RELATIONSHIP*1..4]->(m) RETURN n.path AS changed_path, "
        "[] AS related_files, [] AS related_symbols, [] AS confirmed_ui_mappings LIMIT 2",
        "MATCH (n) WHERE n.path IN $changed_paths AND n.revision = $revision "
        "MATCH p=(n)-[:CODE_RELATIONSHIP {kind: 'CALLS'}*0..3]->(m) "
        "RETURN n.path AS changed_path, [] AS related_files, [] AS related_symbols, "
        "[] AS confirmed_ui_mappings LIMIT 2",
    ],
)
def test_graph_retriever_rejects_unsafe_or_unbounded_generated_queries(query):
    retriever = Neo4jCodeGraphRetriever(
        FakeDriver([]), "saleor", "revision", 12, FakePlanner(query), max_query_rows=100
    )

    with pytest.raises(Neo4jGraphRetrievalError):
        retriever.retrieve(_pull_request())


def test_graph_retriever_runs_generated_query_in_read_only_transaction():
    driver = FakeDriver([])
    retriever = Neo4jCodeGraphRetriever(driver, "saleor", "revision", 12, FakePlanner())

    retriever.retrieve(_pull_request())

    assert driver.commits == 3
    assert driver.rollbacks == 0
