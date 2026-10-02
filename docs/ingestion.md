# Run and extend ingestion

## Install and inspect

Run from the repository root with Python 3.11–3.14:

```sh
uv sync --locked --extra openai --extra vector
uv run trace-impact components
uv run trace-impact validate-project projects/saleor/project.json
uv run python examples/custom_parser.py
```

The extension example ingests the supplied synthetic JSON requirements with a user-registered parser. It needs no model or database credentials. Open `examples/custom_parser.py` first in PyCharm; select the repository `.venv` as its interpreter.

## Collect documentation

```sh
uv run trace-impact collect projects/saleor/project.json
```

Save the returned `run_dir`; use that exact directory in every command below. Collection fetches the explicitly listed pages and pinned README, not the entire documentation website. It produces a corpus manifest, inventory, original bytes and normalized chunks under ignored `runs/`.

Each source chooses `loader` and `parser`. Built-ins are `web`, `github_file`, `local_file` and `html`, `markdown`. Project files may be JSON or YAML. The local loader resolves paths relative to the project file. `github_file` options specify `repository`, a 40-character `revision` matching `version`, and repository-relative `path`. Add `raw.githubusercontent.com` to the allowed hosts. Web pages also need their hosts in `allowed_document_hosts`.

The Saleor public documentation is marked `current-online-unpinned`; retrieval timestamps and content hashes record exactly what was read. These current backend docs may differ from the historical storefront behavior and need review. The README is pinned to the baseline upstream commit.

## Configure live services

Copy `.env.example` to `.env` only if you do not already have a local `.env`. Fill it locally; project JSON/YAML must not contain credentials.

- Extraction: `OPENAI_API_KEY`, `INGESTION_MODEL`.
- Embeddings: `OPENAI_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`. Choose a model that supports the configured dimensions; the adapter passes dimensions explicitly.
- Neo4j: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`. Use the actual database name shown by your instance; it is not always `neo4j`. This Aura instance uses its instance ID as the database name. If needed, inspect `SHOW DATABASES` on the `system` database with the authenticated driver.
- Qdrant: leave `QDRANT_URL` blank for local disk storage at `QDRANT_PATH=.vector-store`. For a server, set its URL and optional `QDRANT_API_KEY`.

```sh
uv run trace-impact doctor
```

Doctor reports whether environment values are present. It does not verify credentials, dimensional compatibility or service connectivity. The CLI loads `.env` without overriding existing environment variables.

The Python SDK uses explicit settings. To read environment variables, call `create_pipeline(Settings.from_env())`; load a dotenv file yourself if needed. `create_pipeline()` alone uses default settings, sufficient for collection and custom local plugins.

## Extract requirements and publish Neo4j

Replace `RUN_DIR` below with the collected directory:

```sh
uv run trace-impact extract RUN_DIR --max-chunks 200
uv run trace-impact init-db
uv run trace-impact load-graph RUN_DIR --with-requirements
```

Extraction uses LangChain structured output and validates citations locally. It writes `extraction.json`, immutable per-attempt records under `extractions/`, a response cache, and `review.json`. A partial attempt exits unsuccessfully and cannot publish requirements. Re-run with a sufficient chunk cap to reuse cached responses and complete the corpus.

`load-graph RUN_DIR` without the flag publishes document provenance only. `init-db` verifies the Neo4j connection and creates uniqueness constraints. The graph stores requirement candidates, their source relationships, chunk references, and initial `NOT_EVALUATED` assessments. A graph load does not establish UI coverage.

## Index and search Qdrant

```sh
uv run trace-impact index RUN_DIR --batch-size 16
uv run trace-impact search RUN_DIR "What happens when a voucher is applied?" --limit 5
```

Indexing calls the configured embedding model and stores text/vector records in Qdrant. It writes `vector-index.json` and an embedding cache. An interrupted publication stays `PARTIAL`; repeat the command to reuse embeddings and upsert/verify records again. The embedding cache is within the run directory, not a shared cache across collection runs.

Search returns evidence chunks and references, not a generated answer. It requires the complete index for the exact corpus and embedding profile. Graph traversal, reranking and answer generation are future work. Qdrant local mode supports this single-process development workflow; use a Qdrant server for concurrent application workers.

## Write a plugin

Read [the simple LLD](low-level-design.md), implement the matching interface in your own module, register an instance or lazy factory, and select its name in project config:

```python
pipeline.components.parsers.register("json_features", JsonFeatureParser())
# or, for a provider that should be constructed only when used:
pipeline.components.extractors.register_factory("my_extractor", build_my_extractor)
```

Unknown and duplicate names fail clearly. A new behavior that fits an existing interface needs no pipeline edit. For custom plugins use your Python entry point; the stock CLI loads the built-in registry. Keep implementation versions/fingerprints accurate when changing behavior so caches and snapshots cannot silently mix processing versions.

Artifact storage currently retains a local corpus entry manifest. Remote-only artifact storage requires a further bootstrap change. Plugins are trusted Python application code; the YAML/JSON file cannot load arbitrary modules.

## Verify

```sh
uv run ruff check src tests examples
uv run ruff format --check src tests examples
uv run pytest -q
uv build
```

The Neo4j integration test is skipped unless `RUN_NEO4J_INTEGRATION=1` and credentials are supplied as process environment variables. It writes data under a unique test project namespace; it never clears the database. Local Qdrant tests run without external services and use labeled deterministic embeddings strictly as test fixtures.

The original version 0.2 module paths were replaced by the simpler 0.3 public structure. See `trace_impact`, `trace_impact.models`, and `trace_impact.interfaces`. Existing run artifacts remain readable; the graph now uses `ChunkRef` instead of full-text `Chunk` nodes. No legacy graph data is automatically deleted or migrated.


## View the collected graph

Live document publication is verified in [the recorded evidence](../artifacts/ingestion/neo4j-verification.json). In Aura Query, connect to your instance and run the following to view a sample of the document relationships:

```cypher
MATCH path = (:Project {id: 'saleor-storefront'})-[:HAS_CORPUS]->(:CorpusRun)
             -[:USES_SNAPSHOT]->(:DocumentSnapshot)-[:HAS_CHUNK]->(:ChunkRef)
RETURN path
LIMIT 25;
```

The graph currently has nine Saleor document snapshots and 121 chunk references. Requirement extraction remains pending model configuration. The live integration test creates labeled synthetic requirements in a separate `integration-*` project; those are not extracted Saleor requirements. Use the project filter above to keep the views separate.
