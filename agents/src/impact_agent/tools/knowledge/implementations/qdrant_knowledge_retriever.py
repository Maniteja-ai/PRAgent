"""Retrieve project-scoped passages from the Qdrant collection built by ingestion."""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from qdrant_client import QdrantClient
from qdrant_client import models as qdrant_models

from impact_agent.config.validation.knowledge import KnowledgeConfig
from impact_agent.config.validation.model import ModelConfig
from impact_agent.domain.models import Evidence, PullRequestSnapshot
from impact_agent.tools.knowledge.interface.knowledge_retriever import KnowledgeRetriever


class QdrantRetrievalError(RuntimeError):
    """Vector search or query embedding failed validation or availability checks."""


class _CollectionInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    result: dict[str, object]


class _EmbeddingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    embedding: dict[str, object]


class _QdrantPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")
    payload: dict[str, object] = Field(default_factory=dict)


class _QdrantQueryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    result: dict[str, object]


class QdrantKnowledgeRetriever(KnowledgeRetriever):
    """Embed a PR query, search its configured project collection, and return hashed evidence."""

    def __init__(
        self,
        knowledge: KnowledgeConfig,
        models: ModelConfig,
        runtime_timeout_seconds: float,
        *,
        qdrant_url: str | None,
        qdrant_api_key: str | None,
        gemini_api_key: str,
        client: httpx.Client | None = None,
        local_qdrant_client: QdrantClient | None = None,
    ) -> None:
        if knowledge.provider != "qdrant":
            raise ValueError("QdrantKnowledgeRetriever requires knowledge.provider='qdrant'")
        if qdrant_url is None and local_qdrant_client is None:
            raise ValueError("Qdrant requires a remote URL or local client")
        if (
            qdrant_url is not None
            and not qdrant_url.startswith("https://")
            and not qdrant_url.startswith("http://localhost")
        ):
            raise ValueError("Qdrant URL must use HTTPS (localhost is allowed for tests)")
        if not gemini_api_key.strip():
            raise ValueError("Gemini API key cannot be empty")
        self._knowledge = knowledge
        self._models = models
        self._qdrant_url = qdrant_url.rstrip("/") if qdrant_url is not None else None
        self._qdrant_api_key = qdrant_api_key
        self._gemini_api_key = gemini_api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=runtime_timeout_seconds)
        self._local_qdrant_client = local_qdrant_client
        self._collection = quote(knowledge.collection, safe="")

    def retrieve(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        dimension = self._collection_dimension()
        vector = self._embed_query(self._query_text(pull_request), dimension)
        if len(vector) != dimension:
            raise QdrantRetrievalError(
                "Gemini query vector dimension does not match the Qdrant collection"
            )
        if self._local_qdrant_client is not None:
            try:
                local_response = self._local_qdrant_client.query_points(
                    collection_name=self._knowledge.collection,
                    query=list(vector),
                    query_filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="project_id",
                                match=qdrant_models.MatchValue(value=self._knowledge.project),
                            )
                        ]
                    ),
                    limit=self._knowledge.max_evidence,
                    with_payload=True,
                )
            except Exception as error:
                raise QdrantRetrievalError("Local Qdrant search failed") from error
            return self._to_evidence(
                {
                    "result": {
                        "points": [
                            {"payload": point.payload or {}} for point in local_response.points
                        ]
                    }
                }
            )

        if self._qdrant_url is None:
            raise QdrantRetrievalError("Remote Qdrant URL is unavailable")
        http_response = self._request(
            "POST",
            f"{self._qdrant_url}/collections/{self._collection}/points/query",
            json_body={
                "query": vector,
                "limit": self._knowledge.max_evidence,
                "with_payload": True,
                "filter": {
                    "must": [
                        {
                            "key": "project_id",
                            "match": {"value": self._knowledge.project},
                        }
                    ]
                },
            },
        )
        return self._to_evidence(http_response.json())

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
        if self._local_qdrant_client is not None:
            self._local_qdrant_client.close()

    def _collection_dimension(self) -> int:
        if self._local_qdrant_client is not None:
            try:
                info = self._local_qdrant_client.get_collection(self._knowledge.collection)
                vectors = info.config.params.vectors
                if isinstance(vectors, qdrant_models.VectorParams):
                    return vectors.size
                raise TypeError("Only single-vector Qdrant collections are currently supported")
            except Exception as error:
                raise QdrantRetrievalError(
                    "Local Qdrant collection vector configuration is invalid"
                ) from error
        if self._qdrant_url is None:
            raise QdrantRetrievalError("Remote Qdrant URL is unavailable")
        response = self._request("GET", f"{self._qdrant_url}/collections/{self._collection}")
        try:
            collection = _CollectionInfo.model_validate(response.json())
            config = collection.result["config"]
            if not isinstance(config, dict):
                raise TypeError("Collection configuration is malformed")
            params = config["params"]
            if not isinstance(params, dict):
                raise TypeError("Collection parameters are malformed")
            vectors = params["vectors"]
            if not isinstance(vectors, dict):
                raise TypeError("Only single-vector Qdrant collections are currently supported")
            dimension = vectors.get("size")
            if not isinstance(dimension, int) or dimension < 1:
                raise ValueError("Collection vector size must be positive")
            return dimension
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise QdrantRetrievalError(
                "Qdrant collection vector configuration is invalid"
            ) from error

    def _embed_query(self, text: str, dimension: int) -> tuple[float, ...]:
        url = (
            f"{self._models.api_url.rstrip('/')}/models/"
            f"{quote(self._models.embedding_model, safe='')}:embedContent"
        )
        embedding_text = text
        embedding_config: dict[str, object] = {"outputDimensionality": dimension}
        if self._models.embedding_model == "gemini-embedding-2":
            embedding_text = f"task: search result | query: {text}"
        else:
            if self._models.embedding_task_type is None:
                raise QdrantRetrievalError("Gemini query embedding task type is not configured")
            embedding_config["taskType"] = self._models.embedding_task_type
        response = self._request(
            "POST",
            url,
            headers={"x-goog-api-key": self._gemini_api_key},
            json_body={
                "content": {"parts": [{"text": embedding_text}]},
                **embedding_config,
            },
        )
        try:
            payload = _EmbeddingResult.model_validate(response.json()).embedding
            values = payload["values"]
            if not isinstance(values, list) or not all(
                isinstance(value, (float, int)) for value in values
            ):
                raise TypeError("Embedding values are missing or malformed")
            return tuple(float(value) for value in values)
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise QdrantRetrievalError("Gemini returned an invalid query embedding") from error

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if (
            self._qdrant_url is not None
            and url.startswith(self._qdrant_url)
            and self._qdrant_api_key
        ):
            request_headers["api-key"] = self._qdrant_api_key
        try:
            response = self._client.request(method, url, headers=request_headers, json=json_body)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            raise QdrantRetrievalError(
                f"Knowledge provider returned HTTP {error.response.status_code}"
            ) from error
        except httpx.RequestError as error:
            raise QdrantRetrievalError("Knowledge provider request failed") from error

    @staticmethod
    def _query_text(pull_request: PullRequestSnapshot) -> str:
        changed_paths = "\n".join(file.path for file in pull_request.files)
        return (
            f"Pull request: {pull_request.title}\n{pull_request.description}\n"
            f"Changed files:\n{changed_paths}\nDiff:\n{pull_request.diff}"
        )

    def _to_evidence(self, payload: object) -> tuple[Evidence, ...]:
        try:
            response = _QdrantQueryResponse.model_validate(payload)
            points = response.result["points"]
            if not isinstance(points, list):
                raise TypeError("Qdrant did not return a points list")
            evidence: list[Evidence] = []
            for raw_point in points:
                point = _QdrantPoint.model_validate(raw_point)
                point_payload = point.payload
                record_id = point_payload["record_id"]
                content = point_payload["content"]
                metadata = point_payload.get("metadata", {})
                if not isinstance(record_id, str) or not isinstance(content, str):
                    raise TypeError("Qdrant point has no record ID or text content")
                if point_payload.get("project_id") != self._knowledge.project:
                    raise ValueError("Qdrant returned evidence from another project")
                if not isinstance(metadata, dict):
                    raise TypeError("Qdrant evidence metadata is malformed")
                code_file_id = metadata.get("code_file_id")
                revision = metadata.get("revision")
                if metadata.get("kind") == "code" and isinstance(code_file_id, str):
                    path = metadata.get("path", code_file_id)
                    source = f"{path} [{code_file_id}@{revision}]"
                    content = self._add_ui_hints(content, metadata)
                else:
                    source = metadata.get("location") or metadata.get("path") or record_id
                evidence.append(
                    Evidence(
                        evidence_id=record_id,
                        source=str(source),
                        content=content,
                        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    )
                )
            return tuple(evidence)
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            raise QdrantRetrievalError("Qdrant returned malformed or unscoped evidence") from error

    @staticmethod
    def _add_ui_hints(content: str, metadata: dict[str, object]) -> str:
        routes = metadata.get("ui_route_candidates", ())
        tags = metadata.get("ui_tags", ())
        route_values = QdrantKnowledgeRetriever._string_values(routes)
        tag_values = QdrantKnowledgeRetriever._string_values(tags)
        symbol_name = metadata.get("symbol_name")
        symbol_kind = metadata.get("symbol_kind")
        start_line = metadata.get("start_line")
        end_line = metadata.get("end_line")
        calls = QdrantKnowledgeRetriever._string_values(metadata.get("called_symbols", ()))
        if not route_values and not tag_values and not isinstance(symbol_name, str):
            return content
        hints = [
            "Static UI hints (unconfirmed; derived from source dependencies, not browser-verified):"
        ]
        if isinstance(symbol_name, str):
            kind = f"{symbol_kind} " if isinstance(symbol_kind, str) else ""
            lines = (
                f" lines {start_line}-{end_line}"
                if isinstance(start_line, int) and isinstance(end_line, int)
                else ""
            )
            hints.append(f"Code symbol: {kind}{symbol_name}{lines}")
        if calls:
            hints.append(f"Statically resolved references: {', '.join(calls)}")
        if route_values:
            hints.append(f"Candidate routes: {', '.join(route_values)}")
        if tag_values:
            hints.append(f"Code tags: {', '.join(tag_values)}")
        return f"{content}\n\n" + "\n".join(hints)

    @staticmethod
    def _string_values(value: object) -> tuple[str, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(item for item in value if isinstance(item, str))
        return ()


class QdrantKnowledgeRetrieverFactory:
    """Resolve Qdrant and Gemini credentials from their configured environment names."""

    @staticmethod
    def create(
        knowledge: KnowledgeConfig,
        models: ModelConfig,
        runtime_timeout_seconds: float,
        environment: Mapping[str, str],
        *,
        project_directory: Path | None = None,
    ) -> QdrantKnowledgeRetriever:
        qdrant_url = (
            environment.get(knowledge.qdrant_url_env)
            if knowledge.qdrant_url_env is not None
            else None
        )
        qdrant_api_key = (
            environment.get(knowledge.qdrant_api_key_env)
            if knowledge.qdrant_api_key_env is not None
            else None
        )
        if models.api_key_env is None:
            raise ValueError("Gemini API key environment name is required for query embeddings")
        gemini_api_key = environment.get(models.api_key_env)
        if not gemini_api_key:
            raise ValueError(
                f"Required Gemini API key environment variable is missing: {models.api_key_env}"
            )

        local_client: QdrantClient | None = None
        if qdrant_url:
            if knowledge.qdrant_api_key_env and not qdrant_api_key:
                raise ValueError(
                    "Required Qdrant API key environment variable is missing: "
                    f"{knowledge.qdrant_api_key_env}"
                )
        elif knowledge.qdrant_path is not None:
            if knowledge.qdrant_path.is_absolute():
                local_path = knowledge.qdrant_path
            else:
                if project_directory is None:
                    raise ValueError(
                        "project_directory is required to resolve local Qdrant storage"
                    )
                local_path = project_directory / knowledge.qdrant_path
            if not local_path.is_dir():
                raise ValueError(f"Local Qdrant storage path does not exist: {local_path}")
            local_client = QdrantClient(path=str(local_path))
            if not local_client.collection_exists(knowledge.collection):
                local_client.close()
                raise ValueError(
                    f"Qdrant collection '{knowledge.collection}' is missing from local storage. "
                    "Run ingestion to publish the vector collection first."
                )
        else:
            missing_name = knowledge.qdrant_url_env or "QDRANT_URL"
            raise ValueError(f"Required Qdrant URL environment variable is missing: {missing_name}")
        return QdrantKnowledgeRetriever(
            knowledge,
            models,
            runtime_timeout_seconds,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            gemini_api_key=gemini_api_key,
            local_qdrant_client=local_client,
        )
