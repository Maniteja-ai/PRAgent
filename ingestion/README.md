# Ingestion

The project has one visible flow:

```text
JSON config -> ConfigLoader -> BeanContainer -> IngestionPipeline
                                              |-> documents -> chunks -> embedding provider -> Qdrant
                                              |-> requirements ---------------------> Neo4j
                                              |-> TypeScript code graph ------------> Neo4j
                                              |-> browser UI -> evidence mapping ----> Neo4j
                                              |-> every stage ----------------------> ArtifactStore
                                              `-> stage result ---------------------> EvaluationRecorder
```

## Run ingestion

Use Python 3.11–3.14 and `uv`. From this directory:

```powershell
uv sync --group dev
Copy-Item .env.example .env   # only the first time
# Set GEMINI_API_KEY and Neo4j connection values in .env.
# Qdrant uses the local .vector-store unless a remote URL is configured.
uv run playwright install chromium
```

The Saleor config expects the source checkout at `work/saleor-storefront-upstream`; change
`configs/saleor/input.json` if your checkout is elsewhere. Then choose a run mode:

```powershell
# Full documents + code + embeddings + requirements + UI observations
uv run ingest configs/saleor.json

# Refresh code relationships and browser route mappings only (no embedding calls)
uv run ingest configs/saleor.json --code-ui-only

# Refresh code chunks/vectors and code graph (does call the configured embedding model)
uv run ingest configs/saleor.json --code-index-only

# Resume after embedding from saved artifacts
uv run ingest configs/saleor.json --resume-after-embedding
```

Only one mode flag can be used per command. A full run may call Gemini, use API quota, and write to Qdrant and Neo4j. The selected mode, config and credentials are kept separate: model/provider settings are in `configs/saleor/models.json`, while credentials stay in `.env`. Run the ingestion tests with `uv run pytest -q`; they do not require live model or database credentials.

## Folder map

- `config_loader/`: interface, typed config models and JSON implementation.
- `beans/`: `@component` registration and constructor injection.
- `extractor/documents/`: local file, web and GitHub document extractors.
- `extractor/code/`: source-code extractor.
- `extractor/ui/`: UI evidence extractor.
- `chunking_strategy/`: interface and selectable chunking implementations.
- `embedding/`: interface and embedding implementations.
- `requirements/`: interface and structured requirement extraction.
- `mapping/`: browser-evidence-backed framework-route and explicit component-tag mappings.
- `storage/`: vector and graph store interfaces and implementations.
- `evaluation/`: stage-recording decorator and JSONL implementation.
- `pipeline/`: pipeline interface and orchestration only.

`bootstrap.create_pipeline()` is the composition root. It reads the selected provider names from the typed config and injects their implementations into `IngestionPipeline`.

## Configuration

`configs/saleor.json` is a small manifest. It points to six focused files in `configs/saleor/`:

- `input`: documents, repository code and UI exploration.
- `models`: requirement extraction and embeddings.
- `chunking`: the selected chunking strategy.
- `storage`: artifacts, Qdrant and Neo4j.
- `constraints`: limits, retries, timeouts and failure policy.
- `evaluation`: stage recording and evaluation settings.

The configured dataset manifest is at
[`evaluation/datasets/saleor-retrieval-v0.1/manifest.json`](evaluation/datasets/saleor-retrieval-v0.1/manifest.json).
It contains vector retrieval labels and synthetic graph cases. The dataset is still a draft pending
human review, so it is a starting reference set, not an approved golden release. It does not cover
requirement extraction or real UI mappings. The ingestion package currently records stage execution;
the configured quality metrics need an evaluation runner before they can be scored.

Each file is independently schema-assisted and becomes one typed bean. `JsonConfigLoader` validates
all six and combines them into `ApplicationConfig` before dependency injection. The checked-in Saleor
configuration selects Gemini embeddings and Gemini requirement extraction in `models.json`. Running
ingestion uses model quota; loading and validating configuration do not call models. See
[`evaluation/README.md`](evaluation/README.md) for dataset scope and current evaluation status.

To refresh only the code dependency graph and browser mappings, without model, embedding or vector
database calls, run `uv run ingest configs/saleor.json --code-ui-only`. It republishes the selected
code revision and captured UI routes to Neo4j and stores a dated snapshot under that revision's
`code_ui_refreshes/` artifact folder.

To refresh code search and dependencies without re-embedding documents or extracting requirements,
run `uv run ingest configs/saleor.json --code-index-only`. TypeScript/JavaScript files are parsed
into syntax-sized Qdrant chunks and `CodeFile`/`CodeSymbol` Neo4j nodes. The graph records resolved
`IMPORTS`, `DECLARES`, `CALLS`, `RENDERS`, `EXTENDS`, and `IMPLEMENTS` relationships. Other source
formats use the configured section chunker. Code chunks go to the same Qdrant collection as
documentation; each chunk carries file, symbol, line-range, call-target, and revision metadata so a
retrieved passage can be joined back to the matching Neo4j file. Each run is saved under
`runs/<project>/<revision>/code_index_runs/`.


