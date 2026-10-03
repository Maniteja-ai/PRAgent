# Trace Impact

Trace Impact is the working submission for the Testsigma AI Engineer take-home. It analyzes a
real Saleor Storefront pull request across product documentation, a Neo4j code/UI/requirement
graph, semantic retrieval, and two live storefront builds.

## Reviewer path

1. Read the [design document](deliverables/DESIGN_DOCUMENT.md) or its
   [eight-page PDF](deliverables/Trace-Impact-Design-Document.pdf).
2. Read the [QA-facing PR 1199 sample output](deliverables/SAMPLE_OUTPUT_PR_1199.md).
3. Open the [baseline](https://testsigma-saleor-baseline.vercel.app) and
   [patched](https://testsigma-saleor-patched.vercel.app) storefronts.
4. Follow the commands below to reproduce ingestion, evaluation, or the live PR workflow.
5. Use the [seven-minute walkthrough script](deliverables/LOOM_WALKTHROUGH.md) for the demo.

The latest fresh run is `packages/coordinator/artifacts/submission-pr-1199-02`. It completed the
configured scope and reproduced the voucher apply/remove behavior. The
[submission checklist](deliverables/SUBMISSION_CHECKLIST.md) records the remaining human Loom step.

### Five-minute reviewer run

This deterministic path needs no credentials, browser, Neo4j, vector store, or model quota:

```powershell
cd packages/coordinator
uv sync --locked
uv run --no-sync trace-coordinator run tests/fixtures/configs/demo.json tests/fixtures/requests/demo.json `
  --run-id reviewer-demo --output artifacts/reviewer-demo
Get-Content artifacts/reviewer-demo/report.md
```

For the live baseline/patched Saleor run, install the documented extras, configure `.env`, and use
`configs/saleor-verified.json`. The saved `submission-pr-1199-02` report lets reviewers inspect the
same evidence without consuming service quota.

A configurable Python library organized into **ingestion**, **retrieval**, and **evals**.
Saleor is the first application configuration.

| Part | Responsibility | Code | Guide |
| --- | --- | --- | --- |
| Ingestion | Load documents, extract requirements, embed content, analyze code and publish knowledge | [ingestion](src/trace_impact/ingestion/) | [Ingestion guide](docs/ingestion.md) |
| Retrieval | Search documents and follow code relationships; reranking is optional | [retrieval](src/trace_impact/retrieval/) | [Retrieval guide](docs/retrieval.md) |
| Evals | Validate datasets, score predictions and run benchmarks | [evals](src/trace_impact/evals/) | [Evaluation guide](docs/simple-evaluation.md) |

**Start with the [folder guide](docs/folder-structure.md)** for file responsibilities,
naming conventions and where to make a change. Configuration, schemas and tests
follow these same three areas.

The separate [coordinator package](packages/coordinator/README.md) adds a LangGraph
workflow with persistent per-agent/tool limits and human-review resume. Its current
milestone connects local Git changes, Neo4j, vector retrieval and bounded browser tools; a GitHub metadata adapter is optional. It has its
own environment and does not change the knowledge library's three areas.

## Configuration

| Task | File |
| --- | --- |
| Select Saleor documents, extraction model and embedding model | [project.json](configs/ingestion/saleor/project.json) |
| Select a repository and immutable code revision | [code.json](configs/ingestion/saleor/code.json) |
| Use vector retrieval without reranking | [default.json](configs/retrieval/default.json) |
| Select an optional LLM reranker with its own API-key variable | [llm.json](configs/retrieval/llm.json) |
| Select changed files for a graph query | [saleor-impact.json](configs/retrieval/saleor-impact.json) |
| Configure evaluation experiments | [configs/evals](configs/evals/) |

JSON definitions in [schemas](schemas/) provide editor suggestions and validation.
Credentials belong in local `.env`; [.env.example](.env.example) is the template.
To add an implementation, implement its interface, register it, then select its
provider name in JSON. The [custom parser example](examples/custom_parser.py)
demonstrates this without credentials.

## Run locally

```powershell
uv sync --locked --extra openai --extra gemini --extra vector --extra evals
uv run --no-sync trace-impact validate-project configs/ingestion/saleor/project.json
uv run --no-sync trace-impact collect configs/ingestion/saleor/project.json
```

Collection prints its run directory. Use it for later stages:

```powershell
uv run --no-sync trace-impact extract <run-directory> --max-chunks 200
uv run --no-sync trace-impact index <run-directory>
uv run --no-sync trace-impact load-graph <run-directory> --with-requirements
uv run --no-sync trace-impact retrieve <run-directory> "What are the voucher requirements?"
```

Model and database stages require configured credentials. Collection reads only
explicitly configured sources. Static code analysis also requires the pinned compiler:

```powershell
Push-Location src/trace_impact/ingestion/code/typescript
npm ci --ignore-scripts --no-audit --no-fund
Pop-Location
uv run --no-sync trace-impact analyze-code configs/ingestion/saleor/code.json --output artifacts/code/saleor-baseline.json
```

Set `repository_path` to your checkout; it resolves relative to the code JSON.
See the [graph guide](docs/graph-schema.md) for publication and retrieval.

## Test and evaluate

```powershell
uv sync --locked --extra openai --extra gemini --extra vector --extra evals --extra reranking
uv run --no-sync pytest -m "not integration" -q
uv run --no-sync trace-eval run examples/evaluation/eval.json
uv run --no-sync python -m trace_impact.evals.validation.dataset evaluation/datasets/saleor-retrieval-v0.1
```

The example evaluation uses synthetic saved outputs. Benchmark runners are in
`trace_impact.evals.runners`; see [evaluation commands](docs/simple-evaluation.md).
Live tests and model experiments can consume service quota.

Run the complete saved-evidence campaign from the coordinator package:

```powershell
Push-Location packages/coordinator
uv run --no-sync trace-coordinator evaluate-stability evaluation/coordinator-golden-v1.json --repetitions 100 --output artifacts/coordinator-stability
uv run --no-sync trace-coordinator evaluation-campaign configs/evaluation/final-campaign.json --output artifacts/final-evaluation
Pop-Location
```

The latest [final campaign](packages/coordinator/artifacts/final-evaluation-01/report.md)
passes 17/17 configured checks. Its `PASSED_WITH_LIMITATIONS` status deliberately retains
the dataset-review and real-graph gaps; read those limits before quoting the metrics.

## Current scope and evidence

- Ingestion: nine Saleor documents, 121 indexed chunks and 96 requirement candidates.
  Quote grounding is not semantic approval. See [the saved run](docs/current-ingestion-run.md).
- Retrieval: vector top five by default, optional rerankers and scoped Neo4j code-impact queries.
- Static code graphs are implemented; real UI mappings remain incomplete.
- The coordinator has one guarded, attested Saleor voucher analysis with bounded UI exploration
  and behavioral verification. General accuracy across repositories and PR types remains unmeasured.

See [evaluation results and gaps](docs/evaluation-results.md), [test instructions](docs/testing.md)
and the [production hardening roadmap](docs/production-hardening-roadmap.md). The coordinator now
contains a six-case draft real-PR dataset, pluggable artifact DLP, signed GitHub webhook/comment
integration, and additional Saleor API behavior oracles. Independent human label approval and
browser coverage for the new scenarios remain open. See
[experiment history](evaluation/history/experiments.json). Frozen datasets and historical
measurements are preserved.

Saleor setup context is in [examples/saleor](examples/saleor/), [deployment setup](docs/setup.md)
and [manual validation](docs/validation.md). Older design proposals are labelled in
[docs/archive](docs/archive/).
