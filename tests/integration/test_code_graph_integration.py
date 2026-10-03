"""Opt-in transaction guarantees against the real Neo4j driver and database."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from dotenv import load_dotenv

from tests.support.graph import FIXTURE, ROOT
from trace_impact import Settings, create_pipeline
from trace_impact.ingestion.storage.neo4j_code_store import Neo4jCodeStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_NEO4J_INTEGRATION") != "1", reason="Live Neo4j opt-in required"),
]


def test_live_publication_failure_rolls_back_complete_transaction():
    load_dotenv(ROOT / ".env", override=False)
    snapshot = FIXTURE.model_copy(update={"id": "rollback-test-" + uuid.uuid4().hex})

    class Interrupted(Neo4jCodeStore):
        def _publish(self, tx, snapshot, digest):
            super()._publish(tx, snapshot, digest)
            raise RuntimeError("Synthetic failure before transaction commit")

    with create_pipeline(Settings.from_env()) as app:
        store = app.components.graphs.resolve("neo4j")
        graph = Interrupted(store.driver, store.database, snapshot.id)
        graph.initialize()
        with pytest.raises(RuntimeError, match="before transaction commit"):
            graph.publish(snapshot)
        records, _, _ = store.driver.execute_query(
            "MATCH (n) WHERE n.uid=$id OR n.graph_id=$id RETURN count(n) AS remaining",
            id=snapshot.id,
            database_=store.database,
        )
        assert records[0]["remaining"] == 0
        assert app.publish_code_graph(snapshot)["nodes"] == len(snapshot.nodes)


def test_live_concurrent_conflicting_publications_keep_one_immutable_snapshot():
    load_dotenv(ROOT / ".env", override=False)
    snapshot = FIXTURE.model_copy(update={"id": "concurrency-test-" + uuid.uuid4().hex})
    conflict = snapshot.model_copy(update={"origin": "conflicting-test-origin"})
    with create_pipeline(Settings.from_env()) as app:
        store = app.components.graphs.resolve("neo4j")
        graph = Neo4jCodeStore(store.driver, store.database, snapshot.id)
        graph.initialize()

        def publish(value):
            try:
                return "OK", graph.publish(value)
            except ValueError:
                return "CONFLICT", None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(publish, (snapshot, conflict)))
        assert sorted(status for status, _ in outcomes) == ["CONFLICT", "OK"]
        winner = (snapshot, conflict)[next(i for i, (status, _) in enumerate(outcomes) if status == "OK")]
        assert graph.publish(winner) == next(result for status, result in outcomes if status == "OK")
