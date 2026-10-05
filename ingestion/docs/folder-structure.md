# Folder guide

**Ingestion prepares knowledge. Retrieval finds knowledge. Evals measures results.**
Knowledge-library Python code belongs to `trace_impact`. The independently installable
[coordinator package](../packages/coordinator/README.md) lives under
`packages/coordinator/`, with its own configuration, schemas, tests and environment.
It does not introduce a dependency from this library to LangGraph.

```text
src/trace_impact/
  ingestion/
    config.py          Application inputs and provider settings
    models.py          Documents, chunks, requirements and run records
    interfaces.py      Replaceable ingestion stages and storage contracts
    service.py         Collection, extraction/checkpoints and publication
    schema.py          JSON editor definitions
    metadata.py        User-defined metadata validation
    documents/         Loaders, parsers, chunker and document processor
    requirements/      Extraction providers, prompts and grounding policies
    embeddings/        Embedding providers and vector index publication
    code/              Pinned Git analysis and TypeScript compiler helper
    storage/           Artifact, requirement graph, code graph and vector stores
  retrieval/
    config.py          Retrieval/reranker/selector settings
    models.py          Scope, passages, scores and results
    interfaces.py      Retriever, Reranker and EvidenceSelector contracts
    service.py         Run and validate retrieval stages
    documents/         Search a completed vector index
    graph/             Read Neo4j and traverse dependency relationships
    rerankers/         Identity, Gemini, compatible API and cross-encoder
    selectors.py       Top-k and score-threshold selection
  evals/
    config.py          Evaluation field mappings, predictions and gates
    cli.py             trace-eval commands
    scoring/           Compare predictions against references
    runners/           Graph, vector, reranking and repeated-query experiments
    validation/        Dataset integrity checks
  shared/              Common contracts, graph data, settings and registry
  bootstrap.py         Register built-ins and construct dependencies lazily
  pipeline.py          Public facade used by CLI and library callers
  cli.py               trace-impact commands
```

`shared/` supports these areas; it is not another pipeline. Generic provider settings
and schemas live there so ingestion does not depend on retrieval. The public
`create_pipeline()` factory and CLI commands are retained. The existing
`IngestionPipeline` class name remains for public API compatibility.

## Where to make a change

| Change | Location |
| --- | --- |
| Choose documents, extraction model or embedding model | `configs/ingestion/<application>/project.json` |
| Add a loader/parser/chunker | `ingestion/documents/`; implement its interface and register it |
| Change requirement extraction or grounding | `ingestion/requirements/` |
| Add an embedding provider | `ingestion/embeddings/` |
| Analyze another language | `ingestion/code/` and its registered analyzer |
| Change database writes | `ingestion/storage/` |
| Change document search or graph reads | `retrieval/documents/` or `retrieval/graph/` |
| Turn reranking on or off | `configs/retrieval/` |
| Evaluate exported predictions | `trace-eval run <config>` with JSON field mappings |
| Add an evaluation target or metric | `evals/runners/` or `evals/scoring/` |

Code graph publication is in `ingestion/storage/neo4j_code_store.py`. Graph queries
are in `retrieval/graph/neo4j_reader.py`. Requirement publication has its own
`neo4j_requirement_store.py`. Vector search reads a completed ingestion index.
A future agent can call the existing retrieval service or pipeline methods as a tool.

## Configuration, reference data and outputs

| Folder | Purpose |
| --- | --- |
| `configs/ingestion/` | Application configurations; local example sources stay beside their JSON/YAML |
| `configs/retrieval/` | Default/optional retrieval choices and graph query input |
| `configs/evals/` | Benchmark and scoring configurations |
| `configs/*/validation_schema/` | JSON editor definitions beside their configuration |
| `tests/ingestion/`, `tests/retrieval/`, `tests/evals/` | Behavioral and failure tests |
| `tests/integration/` | Cross-stage checks; external calls require an integration marker and opt-in |
| `tests/support/` | Shared test-only fixtures |
| `evaluation/datasets/` | Frozen inputs, labels, source evidence and review status |
| `evaluation/history/` | Preserved experiment outputs and measurements |
| `evaluation/local/` | Disposable local benchmark indexes |
| `examples/` | Small usage examples and Saleor deployment/reference context |
| `runs/<application>/<run-id>/` | Resumable ingestion data and checkpoints |
| `artifacts/` | Graph exports, test reports, screenshots and verification evidence |
| `docs/archive/` | Historical designs and previous project overview |

`examples/saleor/reference-requirements.json` contains manually curated business
requirements. Python dependencies are in `pyproject.toml` and `uv.lock`.

An ingestion run retains its original ID and paths. `corpus.json` holds documents
and chunks, `extraction.json` holds current candidates, `vector-index.json` records
index completion, and caches support recovery. These are not disposable clutter.
Historical reports can mention old paths and package names: those identify the
version that was evaluated.

## Naming rules

- Python modules/functions and provider IDs use `snake_case`.
- Classes use `PascalCase`, such as `Neo4jCodeStore` and `VectorSearchService`.
- `interfaces.py` contains contracts; `models.py` data; `config.py` settings; `service.py` coordination.
- Implementation filenames identify the technology and responsibility where useful.
- Application configurations keep their local source files together.
- Stable IDs identify runs/snapshots; friendly names describe applications.

The distribution and CLI are `trace-impact`; Python imports use `trace_impact`.
The evaluator import is `trace_impact.evals`. Internal imports from the old
`trace_eval`, `implementations`, `code_graph`, `_workflows` and `_indexing` locations
must use the new owners. The [migration map](reorganization-map.json) lists file moves.

## Reading order in PyCharm

1. `README.md`, then this guide.
2. `configs/ingestion/saleor/project.json` to see user inputs.
3. `pipeline.py`, then `ingestion/service.py` to follow execution.
4. The relevant interface and implementation.
5. The corresponding test file.

Use `src/` as the source root and `tests/` as the test root in PyCharm. Focus daily
browsing on `src/`, `configs/` and `tests/`. Generated stores, caches, runs and archived
reports remain on disk; they are not additional application code.
