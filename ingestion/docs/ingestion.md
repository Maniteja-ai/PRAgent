# Run and extend ingestion

## Simple configuration map

Start with **`configs/ingestion/saleor/ingestion.json`**. It contains only the project name and
references to five focused files:

| File | One responsibility |
| --- | --- |
| `inputs.json` | Documents, repository revision, application URL and UI seeds |
| `processing.json` | Parser/chunker, requirement model, embeddings, code analyzer and mapping policy |
| `storage.json` | Artifact directory, Qdrant, Neo4j and the separate candidate-mapping directory |
| `runtime.json` | Checkpoints, retries, timeouts, limits and failure behavior |
| `evaluation.json` | Stage evidence recording, golden dataset and enabled metrics |

Every file has a JSON Schema for PyCharm completion and rejects unknown fields. Paths belong to
the file in which they appear and are resolved relative to that file. Credentials remain in
environment variables. Validate the whole configuration without contacting providers:

```powershell
uv run trace-impact validate-ingestion configs/ingestion/saleor/ingestion.json
```

Run the configured stages with:

```powershell
uv run trace-impact run-ingestion configs/ingestion/saleor/ingestion.json
```

The command writes `ingestion-manifest.json` to the collected run and records stage timing,
result hashes and failures in the configured evaluation directory. Evaluation scoring remains
offline and uses the configured golden dataset. UI ingestion is currently disabled in the Saleor
input file; the manifest records that as a coverage gap instead of claiming a mapping exists.

## Legacy single-file configuration

Older commands can still start with **`configs/ingestion/saleor/project.json`**.
In PyCharm, expand `configs` > `ingestion` > `saleor` > `project.json`.

### Pick names from editor suggestions

The project JSON contains `"$schema": "../validation_schema/project.schema.json"`.
The schema supplies completion choices for loader, parser, provider, chunker and
storage names. In PyCharm, place the cursor inside the value (for example, after
`"loader": "`) and use **Ctrl+Space** to request completion. This is an editor
suggestion list, not a separate form UI. If the editor has not associated the schema,
select `configs/ingestion/validation_schema/project.schema.json` under **Settings → Languages & Frameworks →
Schemas and DTDs → JSON Schema Mappings**, and map it to `configs/ingestion/saleor/project.json`.
See [PyCharm's JSON schema documentation](https://www.jetbrains.com/help/pycharm/json.html).

Component names are case-insensitive and trim surrounding whitespace:
`"WEB"`, `"Web"`, and `" web "` all select `"web"`. The same rule applies to
registered parser, chunker, extractor, embedding-provider and storage names.
URLs, local paths, model IDs, repository revisions and document text are not changed.

An unknown name still fails before loading any sources and lists available choices;
close spellings may include a suggestion. The library does not silently replace a
misspelled component with a different implementation. Run `validate-project` to check
names against the actual registered components before collecting documents.

Custom registered names remain allowed by the schema, so the editor cannot prove
that every typed name is registered. Register custom implementations in Python first.
Names use letters, digits, underscores or hyphens; names differing only in case or
surrounding spaces refer to the same component and cannot be registered twice.

The schema is generated from Pydantic models and the component registry; it does not
initialize providers or contact external services. To regenerate built-in suggestions:

```powershell
uv run --no-sync trace-impact schema --output configs/ingestion/validation_schema/project.schema.json
```

For custom plugin suggestions, call `project_schema(app.components)` from
`trace_impact.ingestion.schema` after registering your plugins and save the returned dictionary
as JSON. The `$schema` editor reference is excluded from runtime manifests and hashes,
so adding editor completion does not invalidate cached ingestion results.

### Definitions and configurable metadata

Edit JSON directly. `project.json` contains values; `configs/ingestion/validation_schema/project.schema.json`
defines their types, titles, descriptions, defaults, required fields and allowed
choices. Selecting `github_file` also selects its `options` definition (repository,
commit and file path). No browser application or configuration web server is needed.

Custom metadata is optional. Add this top-level section to a project JSON:

```json
"metadata": {
  "fields": [
    {
      "name": "product_area",
      "title": "Product area",
      "description": "Which application feature this source describes.",
      "type": "string",
      "required": true,
      "choices": ["catalog", "checkout", "payments"]
    }
  ],
  "defaults": {"product_area": "catalog"}
}
```

Inside a source, set `"metadata": {"product_area": "checkout"}` to override
the default. Supported types are `string`, `number`, `boolean` and `string_list`.
The library rejects undefined fields, invalid types, values outside the choices,
and missing required values after defaults are applied. System provenance fields
such as `run_id` cannot be replaced by custom metadata.

To include those metadata keys and choices in editor completion, generate a
project-specific schema and set the project's `$schema` to `"./project.schema.json"`:

```powershell
uv run --no-sync trace-impact schema --config configs/ingestion/saleor/project.json --output configs/ingestion/saleor/project.schema.json
uv run --no-sync trace-impact validate-project configs/ingestion/saleor/project.json
```

Regenerate this schema after changing metadata definitions or shared defaults.
Runtime validation always uses the current JSON definitions. Metadata is recorded
in new snapshots/chunks and Qdrant payloads; Neo4j references retain it as a JSON
property. Existing runs are unchanged. Metadata describes evidence; it does not
mark candidates as semantically correct or UI-verified.

### Give a new implementation its own options definition

Register a loader with a Pydantic options model, then regenerate the schema:

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from trace_impact.shared.component_config import ComponentDefinition
from trace_impact.ingestion.schema import project_schema

class MyLoaderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extraction_mode: Literal["text", "sections"] = Field(
        default="sections", description="How this loader should extract content."
    )

# MyLoader implements SourceLoader.load(source, project, config_dir).
pipeline.components.loaders.register_factory(
    "my_loader",
    MyLoader,
    definition=ComponentDefinition(
        title="My loader",
        description="Read documents from my custom source.",
        options_model=MyLoaderOptions,
    ),
)
schema = project_schema(pipeline.components)
```

The source selects `"loader": "my_loader"` and
`"options": {"extraction_mode": "sections"}`. In `MyLoader.load`, parse
`MyLoaderOptions.model_validate(source.options)` to obtain typed values and defaults.
Save the returned schema dictionary as JSON for the editor. Use the same registered
pipeline to validate and ingest; the stock CLI does not automatically import plugins.
Extraction and embedding providers accept the same `definition` argument when
registering a configured factory. Adding an implementation requires Python code;
choosing and configuring it requires only JSON.

| JSON field | What you choose |
| --- | --- |
| `sources` | The exact documents to ingest; add or remove an entry here |
| `sources[].location` | A documentation URL or a local file path |
| `sources[].loader` | `web`, `github_file`, `local_file`, or a registered custom loader |
| `sources[].parser` | `html`, `markdown`, or a registered custom parser |
| `sources[].options` | Loader-specific input, such as GitHub repository, commit and file path |
| `sources[].authority` | Whether the document supports frontend behaviour, backend rules or an API contract |
| `scope` | Product behaviours the requirement-extraction prompt should focus on |
| `chunker`, `max_chunk_chars` | Chunking implementation and target size |
| `extractor` | Requirement-extraction provider, model, output limit and request pacing |
| `embedding_provider` | Embedding provider, model, dimensions and request pacing |
| `storage` | Registered graph, vector and artifact storage implementations |

For example, this **one entry inside `sources`** reads the checkout documentation:

```json
{
  "id": "checkout",
  "location": "https://docs.saleor.io/developer/checkout/overview",
  "loader": "web",
  "parser": "html",
  "authority": "backend_contract",
  "version": "current-online-unpinned",
  "scope": ["cart", "checkout", "channels"]
}
```

For a local Markdown file, use `loader: "local_file"`, `parser: "markdown"`, and
`location: "spec.md"`; put the file beside the project JSON. Keep the other required
source fields. For the README, see the existing `storefront-readme` entry, whose
`github_file` options select the repository, pinned revision and `README.md` path.

Only the explicitly configured documents are loaded. This is not a recursive website
crawl. The top-level `scope` guides model extraction; it does not crop the documents.
Per-source `scope` and `excluded_inputs` are provenance metadata, not content filters.
`repository` and `baseline_url` record application context; they do not currently
trigger code analysis or browser exploration.

There is **no `steps` array in the current schema**. JSON chooses implementations;
CLI commands or Python methods choose which stage to execute:

| Command | Pipeline method | Work performed |
| --- | --- | --- |
| `collect <project.json>` | `collect(...)` | Load → parse → chunk → save corpus |
| `extract <run_dir>` | `extract(...)` | Model extraction → validate evidence → checkpoint |
| `index <run_dir>` | `index(...)` | Embed chunks → write and verify vectors |
| `load-graph <run_dir>` | `publish_graph(...)` | Publish document references; add `--with-requirements` only after extraction completes |

Code entry points: [`config.py`](../src/trace_impact/ingestion/config.py) reads and validates JSON;
[`bootstrap.py`](../src/trace_impact/bootstrap.py) registers built-in implementations;
[`pipeline.py`](../src/trace_impact/pipeline.py) resolves the selections and runs stages;
[`cli.py`](../src/trace_impact/cli.py) exposes the commands.

Keys and database credentials stay in `.env`. Runtime request timeout and retry count
are currently environment settings (`REQUEST_TIMEOUT`, `MODEL_RETRIES`), while model
choices and pacing are in JSON. A collected run freezes its JSON in `corpus.json`:
editing the original project file affects new collections, not an existing resume.
See [the current run](current-ingestion-run.md) before starting a duplicate collection.

## Install and inspect

Run from the repository root with Python 3.11–3.14:

```sh
uv sync --locked --extra openai --extra gemini --extra vector
uv run trace-impact components
uv run trace-impact validate-project configs/ingestion/saleor/project.json
uv run python examples/custom_parser.py
```

The extension example ingests the supplied synthetic JSON requirements with a user-registered parser. It needs no model or database credentials. Open `examples/custom_parser.py` first in PyCharm; select the repository `.venv` as its interpreter.

## Collect documentation

```sh
uv run trace-impact collect configs/ingestion/saleor/project.json
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
    "model": "gemini-3.5-flash-lite",
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

These fields are part of the full project JSON alongside sources, repository and storage. Extraction and embeddings can select different providers. Both support `gemini` and `openai`; extraction also supports `gemini_interactions`, which uses the same Gemini key through the Interactions API. To choose OpenAI, change the provider and model fields, supply appropriate embedding dimensions, and remove the Gemini-only `thinking_level`. Do not put API keys in JSON: extra fields are rejected and the project configuration is retained in run artifacts.

Run `validate-project` to see the selected providers and models before collection. A collection snapshots its project configuration in `corpus.json`; extraction, indexing and search use that saved configuration. After changing provider/model settings, collect a new run and use its returned directory. Editing project JSON does not silently change an older run or invalidate its provenance. `--model` is a legacy convenience for string-based extractor selections only; JSON model settings take precedence.

The `gemini` extractor uses LangChain's Google chat integration. The `gemini_interactions` extractor wraps Google's Interactions SDK in a LangChain Runnable behind the same extractor interface. It sends the extraction JSON schema, explicitly sets `store=false`, rejects non-completed output, and locally validates the JSON before grounding and caching. The Google SDK is pinned to 2.27.0 because its Interactions error types and retry configuration need a version-specific compatibility boundary. HTTP contract tests verify exact retry budgets, including zero retries. The route has a separate cache fingerprint; it does not reinterpret old responses as new model results. Switching routes does not guarantee provider availability.

Gemini Embedding 2 uses the Google SDK behind our `EmbeddingProvider` interface because the installed LangChain embedding wrapper sends the older `task_type` field. Our adapter uses document/query retrieval prefixes and one explicit `Content` per document. This avoids combining multiple chunks into one vector. Its preprocessing version is part of the embedding profile and cache identity.

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

Read [the simple LLD](archive/low-level-design.md), implement the matching interface in your own module, register an instance or lazy factory, and select its name in project config:

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

The original version 0.2 module paths were replaced by the simpler 0.3 public structure. See `trace_impact`, `trace_impact.ingestion.models`, and `trace_impact.ingestion.interfaces`. Existing run artifacts remain readable; the graph now uses `ChunkRef` instead of full-text `Chunk` nodes. No legacy graph data is automatically deleted or migrated.


## View the collected graph

Live document publication is verified in [the recorded evidence](../artifacts/ingestion/neo4j-verification.json). In Aura Query, connect to your instance and run the following to view a sample of the document relationships:

```cypher
MATCH path = (:Project {id: 'saleor-storefront'})-[:HAS_CORPUS]->(:CorpusRun)
             -[:USES_SNAPSHOT]->(:DocumentSnapshot)-[:HAS_CHUNK]->(:ChunkRef)
RETURN path
LIMIT 25;
```

The graph currently has nine Saleor document snapshots and 121 chunk references. The completed run contains 96 extracted candidates, of which 62 passed quote grounding; semantic review remains pending. The live integration test creates labeled synthetic requirements in a separate `integration-*` project; those are not extracted Saleor requirements. Use the project filter above to keep the views separate.
