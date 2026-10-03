"""Fixed, scoped Neo4j queries for code-impact retrieval."""

import json

from neo4j import Query

from trace_impact.shared.graph_models import GraphEdge, GraphNode, Neighbor


class Neo4jGraphReader:
    def __init__(self, driver, database, graph_id, *, timeout_seconds=30):
        if not graph_id or not 0 < timeout_seconds <= 120:
            raise ValueError("Graph identity and bounded timeout are required")
        self.driver, self.database, self.graph_id = driver, database, graph_id
        self.timeout = timeout_seconds

    def _read(self, query, **params):
        records, _, _ = self.driver.execute_query(
            Query(query, timeout=self.timeout),
            graph=self.graph_id,
            database_=self.database,
            routing_="r",
            **params,
        )
        return records

    @staticmethod
    def _node(data):
        return GraphNode(
            **{k: data[k] for k in ("id", "kind", "name", "project_id", "revision")},
            properties=json.loads(data["properties_json"]),
        )

    def lookup(self, query, limit):
        records = self._read(
            "MATCH (:ImpactSnapshot {uid:$graph, status:'COMPLETE'})-[:HAS_ENTITY]->(n:ImpactEntity) "
            "WHERE n.project_id=$project AND n.revision=$revision AND n.kind='CodeSymbol' "
            "AND (n.id IN $ids OR ($name IS NOT NULL AND n.name=$name) OR n.path IN $files) "
            "RETURN properties(n) AS node ORDER BY n.id LIMIT $limit",
            project=query.scope.project_id,
            revision=query.scope.revision,
            ids=list(query.changed_symbol_ids),
            name=query.symbol_lookup.name if query.symbol_lookup else None,
            files=list(query.changed_files),
            limit=limit,
        )
        return tuple(self._node(row["node"]) for row in records)

    def neighbors(self, scope, ids, kind, incoming, limit):
        # Direction selects fixed application Cypher, never a user-generated statement.
        pattern = (
            "(a)<-[r:IMPACT_LINK]-(b:ImpactEntity)" if incoming else "(a)-[r:IMPACT_LINK]->(b:ImpactEntity)"
        )
        records = self._read(
            "MATCH (:ImpactSnapshot {uid:$graph, status:'COMPLETE'})-[:HAS_ENTITY]->(a:ImpactEntity) "
            f"MATCH {pattern} WHERE a.id IN $ids AND a.graph_id=$graph AND b.graph_id=$graph "
            "AND a.project_id=$project AND b.project_id=$project AND a.revision=$revision AND b.revision=$revision "
            "AND r.graph_id=$graph AND r.type=$kind AND r.status IN $statuses "
            "RETURN properties(b) AS node, properties(r) AS edge ORDER BY b.id, r.id LIMIT $limit",
            project=scope.project_id,
            revision=scope.revision,
            ids=list(ids),
            kind=kind,
            statuses=list(scope.mapping_statuses),
            limit=limit,
        )
        return tuple(
            Neighbor(
                node=self._node(r["node"]),
                edge=GraphEdge(
                    **{k: r["edge"][k] for k in ("id", "source", "target", "type", "status")},
                    properties=json.loads(r["edge"]["properties_json"]),
                ),
            )
            for r in records
        )
