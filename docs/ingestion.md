# Run and extend ingestion

## Install and inspect

Run from the repository root with Python 3.11–3.14:

```sh
uv sync --locked --extra openai --extra gemini --extra vector
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

- Gemini (selected in the Saleor JSON): `GEMINI_API_KEY`. Use a Google AI Studio project on the Free tier to follow this assignment's zero-cost preference; the library does not enable billing or switch providers automatically. API access and quotas still depend on your account.
- OpenAI: `OPENAI_API_KEY` when its provider is selected. The JSON supplies model names and dimensions.
- Legacy string selections still use `INGESTION_MODEL`, `EMBEDDING_MODEL`, and `EMBEDDING_DIMENSIONS` from the environment. These do not override explicit JSON model choices.
- Neo4j: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`. Use the actual database name shown by your instance; it is not always `neo4j`. This Aura instance uses its instance ID as the database name. If needed, inspect `SHOW DATABASES` on the `system` database with the authenticated driver.
- Qdrant: leave `QDRANT_URL` blank for local disk storage at `QDRANT_PATH=.vector-store`. For a server, set its URL and optional `QDRANT_API_KEY`.

```sh
uv run trace-impact doctor
```

Doctor reports whether environment values are present. It does not verify credentials, dimensional compatibility or service connectivity. The CLI loads `.env` without overriding existing environment variables.

The Python SDK uses explicit settings. To read environment variables, call `create_pipeline(Settings.from_env())`; load a dotenv file yourself if needed. `create_pipeline()` alone uses default settings, sufficient for collection and custom local plugins.

## Choose the provider directly in JSON

The current Saleor project selects:

```json
{
  "extractor": {
    "provider": "gemini",
    "model": "gemini-3.7-flash",
    "thinking_level": "low",
    "max_output_tokens": 6000,
    "requests_per_minute": 5
  },
  "embedding_provider": {
    "provider": "gemini",
    "model": "gemini-embedding-2",
    "dimensions": 768,
    "requests_per_minute": 10
  }
}
```

These fields are part of the full project JSON alongside sources, repository and storage. Extraction and embeddings can select different providers. Both support `gemini` and `openai`. To choose OpenAI, change the provider and model fields, supply appropriate embedding dimensions, and remove the Gemini-only `thinking_level`. Do not put API keys in JSON: extra fields are rejected and the project configuration is retained in run artifacts.

Run `validate-project` to see the selected providers and models before collection. A collection snapshots its project configuration in `corpus.json`; extraction, indexing and search use that saved configuration. After changing provider/model settings, collect a new run and use its returned directory. Editing project JSON does not silently change an older run or invalidate its provenance. `--model` is a legacy convenience for string-based extractor selections only; JSON model settings take precedence.

Gemini extraction uses LangChain native structured output. Gemini Embedding 2 uses the Google SDK behind our `EmbeddingProvider` interface because the installed LangChain embedding wrapper sends the older `task_type` field. Our adapter uses document/query retrieval prefixes and one explicit `Content` per document. This avoids combining multiple chunks into one vector. Its preprocessing version is part of the embedding profile and cache identity.

`requests_per_minute` spaces application calls within each provider instance; `0` disables pacing. The configured values are conservative starting settings, not a statement of your actual quota. Server retries, multiple processes, per-token limits and daily quotas remain separate. A provider failure stops extraction with a saved partial checkpoint; retry after fixing access or after quota resets. Already successful extraction results and embeddings are reused. No paid provider fallback is performed.

To add another configured provider in your Python entry point:

```python
pipeline.components.extractors.register_configured_factory(
    "my_provider", lambda config: MyExtractor(model=config.model)
)
```

Then select an `extractor` object with `provider: my_provider` and its model in JSON. Existing instance registrations and string selections still work for custom plugins.

Google references: [LangChain chat integration](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai), [Embedding 2 task formatting](https://ai.google.dev/gemini-api/docs/embeddings), and [account-specific rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

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
