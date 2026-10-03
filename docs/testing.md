# Ingestion testing

## After the three-part reorganization

The final migration checks passed **253 offline tests** and **six live integration
tests**, with **91.35% combined statement-and-branch library coverage**. Experiment
drivers in `evals/runners/` are measured separately, as they were when in `examples/`.
The first all-package coverage report included those drivers and was 75.77%; it is
retained with the migration evidence. The final dataset tests import the packaged
validator directly, so its executed code is included in library coverage.

The relocated graph runner matched all 20 fixture cases; the vector runner executed
40 searches with cached embeddings and unchanged scores. A three-attempt smoke of
the relocated stability runner passed. This is separate from the earlier 100-run
experiment. The saved 121-chunk index remains usable, and reanalysis produced the
identical 2,101-node, 5,365-edge Saleor graph. Wheel packaging and both CLI entry points
were checked. See [folder responsibilities](folder-structure.md) and the
[migration report](../artifacts/reorganization/summary.json).

The suite covers the implemented ingestion library: configuration, loading, parsing,
chunking, extraction, evidence validation, embedding/indexing, artifacts, and graph
publication. It does not establish that all future assignment capabilities are complete.
It now also covers default vector retrieval, optional rerankers, static compiler
extraction and bounded graph traversal. Live graph quality checks are separate from
offline code coverage and are described below.

## Latest evaluation campaign

See [the consolidated results](evaluation-results.md): **250 offline tests** and
**six distinct live integration checks** pass. The fresh benchmarks cover 20 graph
cases, 40 vector queries, two isolation cases and 100 repetitions of one graph query.
Combined-answer and semantic-quality evaluations remain incomplete; their missing
implementations and reference data are listed explicitly in that report.

## Previous vector-default and code-graph checks

The previous full offline run passed **238 tests**, with four external-service tests
deselected and **89.83% combined statement-and-branch coverage** of `trace_impact`.
Live Neo4j benchmark execution is separate and is not included in that offline
coverage number. All 20 frozen graph cases also passed against real Aura, with
100% precision/recall for the synthetic UI, flow and requirement sets and zero status
mismatches. Witness validation, repeated publication and immutable-content guards
passed. These are contract results, not real UI-impact accuracy.

The compiler test uses a real temporary Git repository and the pinned TypeScript
compiler to check aliases, shadowed names, excluded files, source limits and dirty
worktree isolation. Traversal tests cover the frozen cases, budgets, scope leakage
and failures. Real Saleor graph publication and retrieval were verified separately.
See [graph configuration and limitations](graph-schema.md).

The no-config `retrieve` CLI successfully returned five real indexed passages with
identity scoring and no model reranking. Its rerank stage took about 0.15 ms; the
query embedding/vector stage took about 2.81 seconds in this single smoke. This is
not a performance distribution. The smoke exposed a Windows cp1252 output failure
on a zero-width character. JSON output now escapes Unicode without changing decoded
content, and a regression test verifies that behavior.

Final evidence is archived in
`evaluation/history/default-vector-retrieval-2026-10-02/`, including JUnit and coverage.
Lint, dataset structural checks and wheel build passed. The wheel includes the Node
helper and dependency lock while excluding `node_modules`. CI now installs the pinned
compiler dependency before the offline tests; no new remote CI run is claimed.

## Historical ingestion results — 2 October 2026

| Check | Result | What actually ran |
| --- | --- | --- |
| Offline suite | 106 passed; 4 live cases deselected | Unit tests, adapter contracts, local integrations, offline end-to-end flow and configuration completion/normalization |
| Live Neo4j roundtrip | Passed | Real Aura writes, repeat publication, version isolation |
| Full pipeline with live databases | Passed again with Interactions adapter | Local Markdown, mock HTTPS HTML and pinned GitHub README; controlled Gemini HTTP responses; real LangChain/Google SDKs; real Aura and disk Qdrant |
| Live Gemini embeddings and indexing | Passed | Real Gemini Embedding 2, 768 dimensions, disk Qdrant reopened and verified |
| Fully live Gemini → Neo4j/Qdrant | Passed with Gemini 3.5 Flash-Lite | Real extraction and Gemini Embedding 2, Aura publication/citation checks, idempotent graph writes, disk Qdrant reopened and verified |
| Ruff | Passed | Source, tests and examples |

The initial Gemini 3.7 fully live failure remains recorded as a **failure** in the historical report. The later Flash-Lite test passed with real model and database calls; no mock responses were used in that test.
The separate controlled-response database pipeline uses clearly labeled synthetic model responses.
Live test documents are synthetic fixtures, not discovered Saleor requirements.
The separate [full Saleor ingestion run](current-ingestion-run.md) completed all 121 chunks and passed database read-back checks. Its 96 candidates include 34 rejected for evidence review. Completion does not establish semantic correctness or UI coverage; no exhaustive semantic-quality evaluation is claimed.

Offline coverage across `src/trace_impact`: **94.04% statements**, **79.08% branches**,
**91.30% combined**. The CI minimum is 85% combined. This is not exhaustive testing
or proof of correctness. The HTML report lists the remaining untested paths, including
some CLI dispatch/error paths, adapter failures and externally backed operations.
Live runs were executed separately and are not included in this coverage number.

Generated local evidence (gitignored; regenerated by commands below):

- [Latest offline JUnit results](../artifacts/testing/offline-junit.xml)
- [Latest machine-readable coverage](../artifacts/testing/coverage.xml)
- [Interactions pipeline with real stores](../artifacts/testing/interactions-neo4j-junit.xml)
- [Fully live Flash-Lite, Gemini embeddings, Neo4j and Qdrant](../artifacts/testing/live-flash-lite-junit.xml)
- [Initial live tests, including Gemini failure](../artifacts/testing/live.xml)
- [Live embedding/indexing results](../artifacts/testing/live-embeddings.xml)
- [Complete pipeline with real stores and controlled model responses](../artifacts/testing/live-stores.xml)

## Test organization

| File | Main responsibility |
| --- | --- |
| `tests/ingestion/test_ingestion.py` | Parsing, chunking, source policy, citation grounding, deduplication and extraction resume |
| `tests/ingestion/test_library.py` | Configurable plugins, multiple documents per loader, resource cleanup and real local Qdrant |
| `tests/ingestion/test_architecture.py` | Dependency boundaries, atomic writes, run locks, corrupt caches and provider output contracts |
| `tests/ingestion/test_providers.py` | JSON-selected providers, actual SDK request/response contracts with mock HTTP, malformed output, 429/503, retry and resume |
| `tests/ingestion/test_gemini_interactions.py` | Stateless Interactions wire schema, response validation, incomplete output, exact retry budgets, outage checkpoint/resume and client cleanup |
| `tests/ingestion/test_ingestion_failures.py` | Source size/timeouts/redirects, malformed loader output, corrupted corpus and extraction cache invalidation |
| `tests/ingestion/test_indexing_failures.py` | Vector dimension/count/nonfinite/zero checks, cache identity and collection configuration |
| `tests/ingestion/test_graph.py` | Publication guards, metadata-only ChunkRef storage and live graph idempotency |
| `tests/ingestion/test_cli.py` | CLI validation/collection, exit codes, key redaction and logger cleanup |
| `tests/ingestion/test_configuration.py` | Case normalization, typo hints, schema validity/freshness, custom extensions and unchanged cache identity |
| `tests/integration/test_ingestion_e2e.py` | Complete ingestion, citation preservation, cache reuse, database persistence, optional live service tests |
| `tests/conftest.py` | Blocks socket connections in every unmarked/offline test |

End-to-end flow:

```text
JSON configuration
  → local file / web HTML / pinned GitHub README
  → parse and chunk
  → structured requirements → grounding → deduplication
  → embeddings → Qdrant write and read-back
  → Neo4j requirements and citations
  → repeat operations / reopen vector database / verify provenance
```

The tests found and fixed two issues: malformed corpus relationships were accepted,
and repeated embedded CLI calls could leave a logging handler attached to a closed
stream. Regression checks now cover both.

## Run locally or in PyCharm

Use the project `.venv` interpreter. From the repository directory:

```powershell
uv sync --locked --extra openai --extra gemini --extra vector --extra reranking
Push-Location src/trace_impact/ingestion/code/typescript
npm ci --ignore-scripts --no-audit --no-fund
Pop-Location
uv run --no-sync ruff check src tests examples
uv run --no-sync pytest -q -m 'not integration' --cov=trace_impact --cov-branch --cov-report=term-missing --cov-report=html:artifacts/testing/html --cov-report=json:artifacts/testing/coverage.json --junitxml=artifacts/testing/offline.xml
```

In PyCharm, add a **pytest** configuration targeting `tests`, with the repository as
working directory and `-m "not integration" -q` as additional arguments. The default
suite needs no credentials. It blocks network access even if your shell has keys set.
Run an individual test file or function from its gutter while inspecting the code.

## Run live integration checks

These tests use the local `.env`, the provider/model choices from the Saleor JSON,
and explicitly enabled external services. They consume a small amount of API quota.
The fully live fixture is one short document; SDK retries are bounded to one retry.
No fallback model is substituted when the configured model fails.

```powershell
$env:RUN_NEO4J_INTEGRATION = '1'
$env:RUN_LIVE_INGESTION = '1'
uv run --no-sync python -c "from dotenv import load_dotenv; load_dotenv('.env'); import pytest; raise SystemExit(pytest.main(['-q', '-m', 'integration', '--tb=short', '--junitxml=artifacts/testing/live-all.xml', '-o', 'junit_family=legacy']))"
Remove-Item Env:RUN_NEO4J_INTEGRATION, Env:RUN_LIVE_INGESTION
```

Required settings: `GEMINI_API_KEY`, `NEO4J_URI`, `NEO4J_USERNAME`,
`NEO4J_PASSWORD`, `NEO4J_DATABASE`. Live Qdrant uses a temporary local directory,
regardless of production Qdrant settings. Neo4j writes use unique `integration-*`
project IDs and remain for audit; tests never clear the shared database.

To check only database integration, enable `RUN_NEO4J_INTEGRATION` and leave
`RUN_LIVE_INGESTION` unset. A live provider outage must remain a visible test failure.

## CI and remaining limits

The subsequent separate-provider configuration check passed **212 offline tests**
with **93.05% combined coverage** (four external tests deselected). Its
[validation summary](../evaluation/history/reranker-provider-config-2026-10-02/summary.json)
and archived JUnit/coverage reports cover dedicated-key routing, custom endpoints,
both structured-output modes, batching and failure handling using real SDKs against
mock HTTP. No new live model quality or pricing experiment was run for this change.

The final document-pipeline check on 2 October 2026 passed **197 offline tests** with
**92.92% combined statement-and-branch coverage** of `trace_impact` (four external
integration tests deselected). This includes cross-encoder window coverage through
the final token, bounded batching, missing-window rejection, nonfinite/invalid scores,
model-load and inference errors, pinned revisions, cached-only loading options and
real local Transformer inference with a tiny synthetic model. The synthetic model
tests mechanics; the separate real MiniLM benchmark measures relevance quality.

The real cross-encoder completed 40/40 frozen-candidate queries and one full-index
retrieval over the current 121 chunks. The latter used the real query embedding and
Qdrant adapters, with 14 model windows across ten candidates. No production ingestion
or Neo4j publication was repeated. Earlier live ingestion verification remains in the
current-run report. Dataset structural validation, Ruff and offline wheel build passed.
The cross-encoder's lower accuracy is preserved in [experiment history](retrieval-experiment-history.md).

JUnit and coverage evidence are archived in
`evaluation/history/cross-encoder-2026-10-02/validation/`. Reproduce the offline check
after installing all four optional extras (`openai`, `gemini`, `vector`, `reranking`):

```powershell
uv run --no-sync pytest -q -m "not integration" --cov=trace_impact --cov-branch
```

CI installs the reranking extra and uses CPU Torch wheels on Linux. No model weights
are downloaded by offline tests; the real-model benchmark is an explicit command.

The GitHub workflow runs the offline suite and coverage gate on Python 3.12, and
uploads coverage XML plus JUnit results even on failure. Workflow changes from this
test pass are local until committed and pushed; no new remote CI result is claimed.
Live tests are opt-in and are not silently run on pull requests with real credentials.

Local runs used Python 3.14.7. They emit a Google SDK deprecation warning and sometimes
a Qdrant temporary SQLite connection ResourceWarning (the installed dependency's
thread-safety probe). Tests close the actual database clients and verify persisted
vectors after reopening. These dependency warnings have not been suppressed or fixed.

Still outside the verified scope: full Saleor corpus extraction quality, document
crawl freshness, Qdrant server/cloud mode, large-volume/performance testing,
multi-process stress testing, live OpenAI calls, autonomous browser exploration,
exhaustive code dependency extraction, real requirement-to-UI/code mapping, combined
graph/vector reasoning, and validated PR impact analysis. Static code ingestion and
synthetic Neo4j traversal are now tested; they do not close these broader gaps.
