"""Retrieve only revision-matched code dependencies and confirmed UI mappings from Neo4j."""

import hashlib
import json
import re
from collections.abc import Mapping
from time import monotonic
from typing import Any, cast

from neo4j import READ_ACCESS, Driver, GraphDatabase, Record

from impact_agent.config.validation.graph_database import GraphDatabaseConfig
from impact_agent.config.validation.model import ModelConfig
from impact_agent.domain.models import ConfirmedCodeUiMapping, Evidence, PullRequestSnapshot
from impact_agent.domain.pipeline_enums import CoverageGap
from impact_agent.model.implementations.gemini_graph_query_planner import (
    GeminiGraphQueryPlannerFactory,
)
from impact_agent.model.interface.graph_query_planner import GraphQueryPlanner
from impact_agent.tools.knowledge.interface.knowledge_retriever import KnowledgeRetriever

_NODE_SCHEMA_QUERY = """
CALL db.schema.nodeTypeProperties()
YIELD nodeLabels, propertyName, propertyTypes
RETURN nodeLabels, propertyName, propertyTypes
LIMIT $limit
"""
_RELATIONSHIP_SCHEMA_QUERY = """
CALL db.schema.relTypeProperties()
YIELD relType, propertyName, propertyTypes
RETURN relType, propertyName, propertyTypes
LIMIT $limit
"""
_REQUIRED_ALIASES = (
    "changed_path",
    "related_files",
    "related_symbols",
    "confirmed_ui_mappings",
)
_FORBIDDEN_CYPHER = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|ALTER|GRANT|DENY|REVOKE|"
    r"LOAD|CALL|YIELD|USE|TERMINATE|FOREACH|UNION|SHOW)\b",
    re.IGNORECASE,
)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.IGNORECASE)
_VARIABLE_LENGTH_PATH = re.compile(
    r"\[[^\]]*\*(?:(\d+)\s*\.\.\s*(\d+)|\.\.\s*(\d+)|(\d+))?[^\]]*\]"
)
_INVALID_VARIABLE_LENGTH_PROPERTY_MAP = re.compile(
    r"\[[^\]]*\{[^}]*\}[^\]]*\*[^\]]*\]", re.IGNORECASE
)


class Neo4jGraphRetrievalError(RuntimeError):
    """Neo4j returned an invalid or unavailable code impact graph result."""


class Neo4jCodeGraphRetriever(KnowledgeRetriever):
    """Use exact changed paths and the configured indexed revision to avoid false joins."""

    def __init__(
        self,
        driver: Driver,
        database: str,
        indexed_revision: str,
        max_evidence: int,
        planner: GraphQueryPlanner,
        *,
        max_query_rows: int = 100,
        query_timeout_seconds: float = 20,
        max_schema_items: int = 300,
        schema_cache_seconds: int = 300,
        max_call_depth: int = 3,
    ) -> None:
        self._driver = driver
        self._database = database
        self._revision = indexed_revision
        self._max_evidence = max_evidence
        self._planner = planner
        self._max_query_rows = max_query_rows
        self._query_timeout_seconds = query_timeout_seconds
        self._max_schema_items = max_schema_items
        self._schema_cache_seconds = schema_cache_seconds
        self._max_call_depth = max_call_depth
        self._schema_cache: tuple[float, str] | None = None

    def retrieve(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        paths = sorted({file.path for file in pull_request.files})
        if not paths:
            return ()
        schema = self._get_schema()
        try:
            query = self._planner.create_query(
                pull_request,
                schema,
                self._revision,
                self._max_query_rows,
                self._max_call_depth,
            )
            self._validate_query(query)
            records = self._read(
                query,
                {"changed_paths": paths, "revision": self._revision},
                row_limit=self._max_query_rows,
            )
        except Neo4jGraphRetrievalError:
            raise
        except Exception as error:
            raise Neo4jGraphRetrievalError("Neo4j code impact query failed") from error

        found_paths: set[str] = set()
        grouped_records: dict[str, dict[str, object]] = {}
        for record in records:
            data = record.data()
            changed_path = data.get("changed_path")
            if not isinstance(changed_path, str):
                continue
            if changed_path not in paths:
                continue
            found_paths.add(changed_path)
            grouped = grouped_records.setdefault(
                changed_path,
                {
                    "changed_path": changed_path,
                    "related_files": [],
                    "related_symbols": [],
                    "confirmed_ui_mappings": [],
                },
            )
            for field in ("related_files", "related_symbols", "confirmed_ui_mappings"):
                values = data.get(field, [])
                accumulated = grouped[field]
                if not isinstance(values, list) or not isinstance(accumulated, list):
                    grouped[field] = values
                    continue
                unique_values = {
                    json.dumps(value, sort_keys=True, default=str) for value in accumulated
                }
                for value in values:
                    serialized = json.dumps(value, sort_keys=True, default=str)
                    if serialized not in unique_values:
                        accumulated.append(value)
                        unique_values.add(serialized)

        graph_evidence = [
            self._to_evidence(grouped_records[path]) for path in sorted(grouped_records)
        ]
        unmapped_paths = [
            path
            for path, data in grouped_records.items()
            if not self._has_confirmed_mapping(path, data.get("confirmed_ui_mappings", []))
        ]

        missing_paths = [path for path in paths if path not in found_paths]
        coverage_evidence: list[Evidence] = []
        if missing_paths:
            coverage_evidence.append(
                self._coverage_evidence(
                    CoverageGap.CODE_GRAPH_DATA_MISSING,
                    missing_paths,
                    "No indexed code graph node matched these changed files at the configured "
                    "revision. This is a coverage gap, not evidence that the files have no UI impact.",
                )
            )
        if unmapped_paths:
            coverage_evidence.append(
                self._coverage_evidence(
                    CoverageGap.CODE_TO_UI_MAPPING_MISSING,
                    unmapped_paths,
                    "No confirmed code-to-UI mapping was returned for these indexed changed files. "
                    "This is a coverage gap, not evidence that the files have no UI impact.",
                )
            )
        return tuple(graph_evidence[: self._max_evidence] + coverage_evidence)

    def close(self) -> None:
        self._planner.close()
        self._driver.close()

    def _get_schema(self) -> str:
        now = monotonic()
        if (
            self._schema_cache is not None
            and now - self._schema_cache[0] < self._schema_cache_seconds
        ):
            return self._schema_cache[1]
        nodes = self._read(
            _NODE_SCHEMA_QUERY,
            {"limit": self._max_schema_items},
            row_limit=self._max_schema_items,
        )
        relationships = self._read(
            _RELATIONSHIP_SCHEMA_QUERY,
            {"limit": self._max_schema_items},
            row_limit=self._max_schema_items,
        )
        schema = json.dumps(
            {
                "nodes": [record.data() for record in nodes],
                "relationships": [record.data() for record in relationships],
            },
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        self._schema_cache = (now, schema)
        return schema

    def _read(
        self, query: str, parameters: Mapping[str, object], *, row_limit: int
    ) -> list[Record]:
        try:
            with self._driver.session(
                database=self._database,
                default_access_mode=READ_ACCESS,
                fetch_size=row_limit,
            ) as session:
                transaction = session.begin_transaction(timeout=self._query_timeout_seconds)
                try:
                    result = transaction.run(query, cast(dict[str, Any], dict(parameters)))
                    records = list(result)
                    if len(records) > row_limit:
                        raise Neo4jGraphRetrievalError(
                            "Neo4j query exceeded the configured row limit"
                        )
                    transaction.commit()
                    return records
                except Exception:
                    transaction.rollback()
                    raise
        except Neo4jGraphRetrievalError:
            raise
        except Exception as error:
            raise Neo4jGraphRetrievalError("Neo4j read-only query failed") from error

    def _validate_query(self, query: str) -> None:
        if not query or len(query) > 20_000:
            raise Neo4jGraphRetrievalError("Generated Cypher is empty or too large")
        if any(marker in query for marker in (";", "//", "/*", "*/", "`")):
            raise Neo4jGraphRetrievalError(
                "Generated Cypher contains a disallowed statement marker"
            )
        if _FORBIDDEN_CYPHER.search(query):
            raise Neo4jGraphRetrievalError("Generated Cypher contains a disallowed clause")
        if _INVALID_VARIABLE_LENGTH_PROPERTY_MAP.search(query):
            raise Neo4jGraphRetrievalError(
                "Generated Cypher cannot combine relationship property maps with variable-length paths"
            )
        if not re.match(r"^\s*MATCH\b", query, re.IGNORECASE):
            raise Neo4jGraphRetrievalError("Generated Cypher must begin with MATCH")
        if "$changed_paths" not in query or "$revision" not in query:
            raise Neo4jGraphRetrievalError(
                "Generated Cypher must use PR path and revision parameters"
            )
        for alias in _REQUIRED_ALIASES:
            if not re.search(rf"\bAS\s+{alias}\b", query, re.IGNORECASE):
                raise Neo4jGraphRetrievalError(f"Generated Cypher must return {alias}")
        for path_match in _VARIABLE_LENGTH_PATH.finditer(query):
            upper_bound = path_match.group(2) or path_match.group(3) or path_match.group(4)
            if upper_bound is None:
                raise Neo4jGraphRetrievalError(
                    "Generated Cypher must bound every variable-length relationship path"
                )
            if int(upper_bound) > self._max_call_depth:
                raise Neo4jGraphRetrievalError(
                    "Generated Cypher exceeds the configured code relationship depth"
                )
        limits = list(_LIMIT.finditer(query.strip()))
        if len(limits) != 1 or int(limits[0].group(1)) > self._max_query_rows:
            raise Neo4jGraphRetrievalError("Generated Cypher must end with a bounded numeric LIMIT")

    def _to_evidence(self, record: Mapping[str, object]) -> Evidence:
        changed_path = record.get("changed_path")
        related_files = record.get("related_files", [])
        related_symbols = record.get("related_symbols", [])
        mappings = record.get("confirmed_ui_mappings", [])
        if not isinstance(changed_path, str):
            raise Neo4jGraphRetrievalError("Neo4j returned a code node without a path")
        related_text = self._format_related_files(related_files)
        symbol_text = self._format_related_symbols(related_symbols)
        mapping_text = self._format_mappings(changed_path, mappings)
        content = (
            f"Indexed code graph at revision {self._revision}.\n"
            f"Changed code file: {changed_path}\n"
            f"Connected code files: {related_text}\n"
            f"Related code symbols: {symbol_text}\n"
            f"Confirmed UI mappings: {mapping_text}"
        )
        identifier = hashlib.sha256(
            f"{self._revision}:{changed_path}:{content}".encode()
        ).hexdigest()
        return Evidence(
            evidence_id=f"neo4j:{identifier}",
            source=f"neo4j://{self._database}/{changed_path}",
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            confirmed_code_ui_mappings=self._confirmed_mappings(changed_path, mappings),
        )

    @staticmethod
    def _confirmed_mappings(changed_path: str, value: object) -> tuple[ConfirmedCodeUiMapping, ...]:
        if not isinstance(value, list):
            raise Neo4jGraphRetrievalError("Neo4j returned malformed UI mappings")
        mappings: list[ConfirmedCodeUiMapping] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            code_path = item.get("code_path")
            url = item.get("url")
            if code_path != changed_path:
                continue
            evidence_ids = item.get("evidence_ids", [])
            if not isinstance(code_path, str) or not isinstance(url, str):
                continue
            if not isinstance(evidence_ids, list) or not all(
                isinstance(evidence_id, str) for evidence_id in evidence_ids
            ):
                raise Neo4jGraphRetrievalError("Neo4j returned malformed UI mapping evidence")
            mappings.append(
                ConfirmedCodeUiMapping(
                    changed_path=changed_path,
                    code_path=code_path,
                    url=url,
                    evidence_ids=tuple(evidence_ids),
                    query_parameter_names=tuple(
                        sorted(
                            name
                            for name in item.get("query_parameter_names", [])
                            if isinstance(name, str) and name
                        )
                    )
                    if isinstance(item.get("query_parameter_names", []), list)
                    else (),
                )
            )
        return tuple(mappings)

    @staticmethod
    def _has_confirmed_mapping(changed_path: str, value: object) -> bool:
        if not isinstance(value, list):
            raise Neo4jGraphRetrievalError("Neo4j returned malformed UI mappings")
        return any(
            isinstance(item, Mapping)
            and item.get("code_path") == changed_path
            and isinstance(item.get("url"), str)
            for item in value
        )

    @staticmethod
    def _coverage_evidence(gap: CoverageGap, paths: list[str], message: str) -> Evidence:
        shown_paths = paths[:20]
        remaining_count = len(paths) - len(shown_paths)
        path_summary = ", ".join(shown_paths)
        if remaining_count:
            path_summary += f", and {remaining_count} more"
        content = f"{message}\nAffected paths: {path_summary}"
        digest = hashlib.sha256(f"{gap.value}:{content}".encode()).hexdigest()
        return Evidence(
            evidence_id=f"coverage-gap:{digest}",
            source="neo4j-coverage://changed-files",
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            coverage_gap=gap,
        )

    @staticmethod
    def _format_related_files(value: object) -> str:
        if not isinstance(value, list):
            raise Neo4jGraphRetrievalError("Neo4j returned malformed code relationships")
        formatted: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                path = item.get("path")
                kinds = item.get("relationship_kinds", [])
                if isinstance(path, str):
                    formatted.append(f"{path} via {', '.join(str(kind) for kind in kinds)}")
        return "; ".join(sorted(set(formatted))) or "No related file returned"

    @staticmethod
    def _format_related_symbols(value: object) -> str:
        if not isinstance(value, list):
            raise Neo4jGraphRetrievalError("Neo4j returned malformed code symbols")
        formatted: list[str] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            kind = item.get("symbol_kind")
            path = item.get("path")
            start_line = item.get("start_line")
            end_line = item.get("end_line")
            relationships = item.get("relationship_kinds", [])
            if not isinstance(name, str) or not isinstance(path, str):
                continue
            span = (
                f":{start_line}-{end_line}"
                if isinstance(start_line, int) and isinstance(end_line, int)
                else ""
            )
            relation_values = relationships if isinstance(relationships, list) else []
            relation_text = ", ".join(str(relationship) for relationship in relation_values)
            label = f"{kind} " if isinstance(kind, str) and kind else ""
            edge = f" via {relation_text}" if relation_text else ""
            formatted.append(f"{label}{name} ({path}{span}){edge}")
        return "; ".join(sorted(set(formatted))) or "No related symbols returned"

    @staticmethod
    def _format_mappings(changed_path: str, value: object) -> str:
        if not isinstance(value, list):
            raise Neo4jGraphRetrievalError("Neo4j returned malformed UI mappings")
        formatted: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                url = item.get("url")
                code_path = item.get("code_path")
                if code_path != changed_path:
                    continue
                confidence = item.get("confidence")
                title = item.get("title")
                basis = item.get("basis")
                evidence_ids = item.get("evidence_ids", [])
                if isinstance(url, str):
                    page_label = f"{title} at {url}" if isinstance(title, str) and title else url
                    code_label = f"from {code_path} " if isinstance(code_path, str) else ""
                    mapping_basis = (
                        f"{basis}; a route mapping identifies the page, not individual controls"
                        if basis == "framework_route"
                        else str(basis)
                    )
                    evidence_count = len(evidence_ids) if isinstance(evidence_ids, list) else 0
                    query_parameters = (
                        [
                            name
                            for name in item.get("query_parameter_names", [])
                            if isinstance(name, str) and name
                        ]
                        if isinstance(item.get("query_parameter_names", []), list)
                        else []
                    )
                    parameter_text = (
                        f"; query parameters: {', '.join(query_parameters)}"
                        if query_parameters
                        else ""
                    )
                    formatted.append(
                        f"{code_label}-> {page_label} ({mapping_basis}; confidence {confidence}; "
                        f"{evidence_count} evidence references{parameter_text})"
                    )
        return "; ".join(sorted(set(formatted))) or "No confirmed UI mapping returned"


class Neo4jCodeGraphRetrieverFactory:
    """Create the graph reader using connection variable names from graph_database.json."""

    @staticmethod
    def create(
        config: GraphDatabaseConfig,
        max_evidence: int,
        environment: Mapping[str, str],
        model_config: ModelConfig,
        model_timeout_seconds: float,
    ) -> Neo4jCodeGraphRetriever | None:
        if not config.enabled or config.provider == "disabled":
            return None
        required_names = (
            config.uri_env,
            config.username_env,
            config.password_env,
            config.database_env,
        )
        values: list[str] = []
        for name in required_names:
            value = environment.get(name)
            if not value:
                raise ValueError(f"Required Neo4j environment variable is missing: {name}")
            values.append(value)
        uri, username, password, database = values
        planner = GeminiGraphQueryPlannerFactory.create(
            model_config, model_timeout_seconds, environment
        )
        try:
            driver = GraphDatabase.driver(uri, auth=(username, password))
        except Exception:
            planner.close()
            raise
        return Neo4jCodeGraphRetriever(
            driver,
            database,
            config.indexed_revision,
            max_evidence,
            planner,
            max_query_rows=config.max_query_rows,
            query_timeout_seconds=config.query_timeout_seconds,
            max_schema_items=config.max_schema_items,
            schema_cache_seconds=config.schema_cache_seconds,
            max_call_depth=config.max_call_depth,
        )
