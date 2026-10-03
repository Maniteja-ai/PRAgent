"""Immutable graph publication and fixed, scoped Neo4j neighborhood queries."""

import hashlib
import json

from neo4j import Query, unit_of_work

from trace_impact.ingestion.models import stable_id


class Neo4jCodeStore:
    def __init__(self, driver, database, graph_id, *, timeout_seconds=30):
        if not graph_id or not 0 < timeout_seconds <= 120:
            raise ValueError("Graph identity and bounded timeout are required")
        self.driver, self.database, self.graph_id = driver, database, graph_id
        self.timeout = timeout_seconds

    def initialize(self):
        for label in ("ImpactSnapshot", "ImpactEntity"):
            self.driver.execute_query(
                Query(
                    f"CREATE CONSTRAINT {label.lower()}_uid IF NOT EXISTS FOR (n:{label}) REQUIRE n.uid IS UNIQUE",
                    timeout=self.timeout,
                ),
                database_=self.database,
            )

    def publish(self, snapshot):
        if snapshot.id != self.graph_id:
            raise ValueError("Snapshot does not belong to selected graph")
        payload = snapshot.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.driver.session(database=self.database) as session:

            @unit_of_work(timeout=self.timeout)
            def publish(tx):
                return self._publish(tx, snapshot, digest)

            return session.execute_write(publish)

    def _publish(self, tx, snapshot, digest):
        def run(text, **kwargs):
            return tx.run(text, **kwargs)

        # This write also serializes concurrent publications of the same immutable identity.
        record = run(
            "MERGE (g:ImpactSnapshot {uid:$id}) ON CREATE SET g.sha256=$hash, g.status='WRITING' "
            "SET g.locked=true RETURN g.sha256 AS hash",
            id=self.graph_id,
            hash=digest,
        ).single(strict=True)
        if record["hash"] != digest:
            raise ValueError("Graph ID already exists with different content; use a new snapshot ID")
        nodes = [
            {
                **n.model_dump(exclude={"properties"}),
                "uid": stable_id(self.graph_id, n.id),
                "properties_json": json.dumps(n.properties),
                "path": n.properties.get("path", ""),
            }
            for n in snapshot.nodes
        ]
        edges = [
            {
                **e.model_dump(exclude={"properties"}),
                "uid": stable_id(self.graph_id, e.id),
                "source_uid": stable_id(self.graph_id, e.source),
                "target_uid": stable_id(self.graph_id, e.target),
                "properties_json": json.dumps(e.properties),
            }
            for e in snapshot.edges
        ]
        for start in range(0, len(nodes), 500):
            run(
                "MATCH (g:ImpactSnapshot {uid:$graph}) UNWIND $rows AS row "
                "MERGE (n:ImpactEntity {uid:row.uid}) SET n += row, n.graph_id=$graph "
                "MERGE (g)-[:HAS_ENTITY]->(n)",
                graph=self.graph_id,
                rows=nodes[start : start + 500],
            ).consume()
        for start in range(0, len(edges), 500):
            run(
                "UNWIND $rows AS row MATCH (a:ImpactEntity {uid:row.source_uid}), (b:ImpactEntity {uid:row.target_uid}) "
                "MERGE (a)-[r:IMPACT_LINK {uid:row.uid}]->(b) SET r += row, r.graph_id=$graph",
                graph=self.graph_id,
                rows=edges[start : start + 500],
            ).consume()
        counts = run(
            "MATCH (g:ImpactSnapshot {uid:$graph})-[:HAS_ENTITY]->(n) "
            "OPTIONAL MATCH (n)-[r:IMPACT_LINK {graph_id:$graph}]->() "
            "RETURN count(DISTINCT n) AS nodes, count(r) AS edges",
            graph=self.graph_id,
        ).single(strict=True)
        if counts["nodes"] != len(nodes) or counts["edges"] != len(edges):
            raise ValueError("Graph publication read-back count mismatch")
        run(
            "MATCH (g:ImpactSnapshot {uid:$graph}) SET g.status='COMPLETE', g.origin=$origin, g.locked=false",
            graph=self.graph_id,
            origin=snapshot.origin,
        ).consume()
        return {
            "graph_id": self.graph_id,
            "nodes": counts["nodes"],
            "edges": counts["edges"],
            "sha256": digest,
        }
