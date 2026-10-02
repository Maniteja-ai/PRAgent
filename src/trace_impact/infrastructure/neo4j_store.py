"""Neo4j persistence. All data values are parameters; labels/queries are fixed application code."""

from __future__ import annotations

import json

from neo4j import GraphDatabase

from ..domain.models import Corpus, ExtractionRun, stable_id
from ..domain.policies import normalized

LABELS = (
    "Project",
    "CorpusRun",
    "Source",
    "DocumentSnapshot",
    "Chunk",
    "ExtractionRun",
    "Requirement",
    "CoverageAssessment",
)


class Neo4jStore:
    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        self.driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=15)
        self.database = database

    def close(self):
        self.driver.close()

    def initialize(self):
        self.driver.verify_connectivity()
        for label in LABELS:
            self.driver.execute_query(
                f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE",
                database_=self.database,
            )

    def load(self, corpus: Corpus, extraction: ExtractionRun | None = None):
        if corpus.errors:
            raise ValueError("Refusing to load a partial corpus")
        if extraction and (
            extraction.corpus_run_id != corpus.run_id or extraction.project_id != corpus.project.project_id
        ):
            raise ValueError("Extraction does not belong to this corpus/project")
        if extraction and extraction.status != "COMPLETE":
            raise ValueError("Refusing to publish a partial extraction; resume extraction first")
        if extraction:
            chunk_ids = {chunk.id for chunk in corpus.chunks}
            if extraction.errors or set(extraction.processed_chunk_ids) != chunk_ids:
                raise ValueError("Complete extraction must account for every corpus chunk without errors")
            if any(
                evidence.chunk_id not in chunk_ids
                for requirement in extraction.requirements
                for evidence in requirement.evidence
            ):
                raise ValueError("Requirement evidence must reference this corpus")
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._load_tx, corpus, extraction)

    @staticmethod
    def _load_tx(tx, corpus: Corpus, extraction: ExtractionRun | None):
        pid = corpus.project.project_id
        chunk_texts = {chunk.id: chunk.text for chunk in corpus.chunks}
        tx.run(
            """MERGE (p:Project {id: $pid}) SET p.name = $name
            MERGE (run:CorpusRun {id: $run})
            SET run.created_at=$created, run.config_sha256=$hash, run.baseline_commit=$commit,
                run.repository_url=$repo, run.baseline_url=$url, run.status='COLLECTED'
            MERGE (p)-[:HAS_CORPUS]->(run)""",
            pid=pid,
            name=corpus.project.name,
            run=corpus.run_id,
            created=corpus.created_at,
            hash=corpus.config_sha256,
            commit=corpus.project.repository.baseline_commit,
            repo=corpus.project.repository.url,
            url=corpus.project.baseline_url,
        ).consume()
        for snapshot in corpus.snapshots:
            props = snapshot.model_dump()
            props.pop("id")
            tx.run(
                """MATCH (p:Project {id:$pid}), (run:CorpusRun {id:$run})
                MERGE (s:Source {id:$sid}) SET s.source_key=$source_key
                MERGE (d:DocumentSnapshot {id:$did}) ON CREATE SET d += $props
                MERGE (p)-[:HAS_SOURCE]->(s)
                MERGE (s)-[:HAS_SNAPSHOT]->(d)
                MERGE (run)-[link:USES_SNAPSHOT]->(d) SET link.retrieved_at=$retrieved
                """,
                pid=pid,
                run=corpus.run_id,
                retrieved=snapshot.retrieved_at,
                sid=stable_id(pid, snapshot.source_id),
                source_key=snapshot.source_id,
                did=snapshot.id,
                props=props,
            ).consume()
        tx.run(
            """UNWIND $chunks AS row
            MATCH (d:DocumentSnapshot {id:row.snapshot_id})
            MERGE (c:Chunk {id:row.id}) SET c += row
            MERGE (d)-[:HAS_CHUNK]->(c)""",
            chunks=[c.model_dump() for c in corpus.chunks],
        ).consume()
        if extraction is None:
            return
        tx.run(
            """MATCH (run:CorpusRun {id:$corpus})
            MERGE (e:ExtractionRun {id:$eid}) SET e += $props
            MERGE (run)-[:HAS_EXTRACTION]->(e)""",
            corpus=corpus.run_id,
            eid=extraction.id,
            props={
                "provider": extraction.provider,
                "model": extraction.model,
                "prompt_version": extraction.prompt_version,
                "status": extraction.status,
                "created_at": extraction.created_at,
                "processed_chunk_ids": extraction.processed_chunk_ids,
                "no_requirement_chunks_json": json.dumps(extraction.no_requirement_chunks),
            },
        ).consume()
        for req in extraction.requirements:
            # Rejected and uncertain candidates remain visible for audit, but aren't declared facts.
            # Validation is run-specific: a later policy/model run must not rewrite earlier findings.
            graph_requirement_id = stable_id(extraction.id, req.id)
            props = req.candidate.model_dump(exclude={"evidence_quote"})
            props.update(candidate_id=req.id, validation=req.validation, review_reasons=req.review_reasons)
            tx.run(
                """MATCH (e:ExtractionRun {id:$eid})
                MERGE (r:Requirement {id:$rid}) SET r += $props
                MERGE (e)-[:PRODUCED]->(r)
                MERGE (a:CoverageAssessment {id:$aid})
                ON CREATE SET a.status='NOT_EVALUATED', a.scope='INGESTION_ONLY',
                    a.reason='No browser crawl has been linked to this requirement'
                MERGE (e)-[:HAS_ASSESSMENT]->(a)
                MERGE (a)-[:ASSESSES]->(r)""",
                eid=extraction.id,
                rid=graph_requirement_id,
                props=props,
                aid=stable_id(extraction.id, req.id, "initial-coverage"),
            ).consume()
            for evidence in req.evidence:
                tx.run(
                    """MATCH (r:Requirement {id:$rid}), (c:Chunk {id:$cid})
                    MERGE (r)-[e:CITES {quote:$quote}]->(c)
                    SET e.quote_verified=$verified, e.semantic_verified=false""",
                    rid=graph_requirement_id,
                    cid=evidence.chunk_id,
                    quote=evidence.quote,
                    verified=len(normalized(evidence.quote)) >= 12
                    and normalized(evidence.quote) in normalized(chunk_texts[evidence.chunk_id]),
                ).consume()

    def counts(self, project_id: str) -> dict:
        records, _, _ = self.driver.execute_query(
            """
            MATCH (p:Project {id:$pid})-[:HAS_CORPUS]->(run)
            OPTIONAL MATCH (run)-[:USES_SNAPSHOT]->(d)-[:HAS_CHUNK]->(c)
            OPTIONAL MATCH (run)-[:HAS_EXTRACTION]->(e)-[:PRODUCED]->(r)
            RETURN count(DISTINCT run) AS corpus_runs, count(DISTINCT d) AS snapshots,
                   count(DISTINCT c) AS chunks, count(DISTINCT r) AS requirements
            """,
            pid=project_id,
            database_=self.database,
        )
        return dict(records[0]) if records else {}
