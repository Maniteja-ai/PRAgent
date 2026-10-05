# Historical project overview

This records the setup and experiments before the folder reorganization. Start with the current [README](../../README.md) and [folder guide](../folder-structure.md).

# Trace Impact — requirements ingestion for UI and code impact analysis

An application-configured foundation for an agent that connects documented requirements, observed UI, and source code to explain a real pull request's impact. Saleor is the first integration, not a hardcoded dependency.

Implemented: a reusable Python ingestion library with configuration-selected loaders, parsers, chunkers, extraction, embeddings and storage. Neo4j holds requirement/evidence relationships; Qdrant holds searchable chunks and vectors. Original evidence and checkpoints stay in local artifacts.

**Document ingestion is complete for the current Saleor run: 9 documents, all 121 chunks indexed in Qdrant, and 96 requirement candidates published to Neo4j.** Of those candidates, 62 passed exact quote grounding and 34 were rejected for evidence review. Database read-back verified the exact run, vectors, candidate flags and citations. Semantic correctness, scope and UI coverage still need review. See the [current run report](../../docs/current-ingestion-run.md) and [test results](../../docs/testing.md), including a passed fully live Gemini → Qdrant/Neo4j integration test. Autonomous browser exploration, real UI/code mapping, combined graph/vector reasoning, full RAG answers and validated PR impact analysis remain future work.

**Document retrieval defaults to vector top five, with no reranking model call.** The [default JSON](../../configs/retrieval/default.json) makes that choice explicit; Gemini, compatible LLM APIs and the local cross-encoder remain opt-in through `--config`. Query embedding still uses its configured provider. See the [measured tradeoffs](../../docs/retrieval-experiment-history.md).

**Static code ingestion and Neo4j impact retrieval are now implemented.** The pinned Saleor graph contains 244 files, 1,857 symbols and 5,365 relationships. A real Neo4j query for the PR files returned 43 potentially affected symbols across seven files, with evidence paths. UI impact remains `UNMAPPED` because real code-to-flow links are not available. All 20 synthetic graph contracts passed against live Neo4j; this does not establish Saleor impact accuracy. See [graph configuration, commands and limitations](../../docs/graph-schema.md#implemented-static-code-graph-and-impact-retrieval).

## Start with the extension example

```sh
uv sync --locked --extra openai --extra gemini --extra vector
uv run python examples/custom_parser.py
uv run trace-impact components
uv run trace-impact collect configs/ingestion/saleor/project.json
```

The example shows the library's main idea: implement a parser, register it under a name, and let user configuration select it. It runs without credentials. Collection works the same way for the Saleor README and public documentation. See the [source inventory](../../artifacts/ingestion/source-inventory.json).

- [Simple LLD: interfaces, flow, storage and extension points](../../docs/archive/low-level-design.md)
- [Working custom parser](../../examples/custom_parser.py) and [its user configuration](../../configs/ingestion/plugin-example/project.yaml)
- [Setup, extraction, indexing and search commands](../../docs/ingestion.md)
- [Code graph ingestion, Neo4j retrieval and remaining UI mappings](../../docs/graph-schema.md)
- [Unit, integration and end-to-end tests](../../docs/testing.md)
- [Saleor configuration](../../configs/ingestion/saleor/project.json)
- [JSON definitions, metadata and plugin options](../../docs/ingestion.md#definitions-and-configurable-metadata) — editor completion and validation directly in JSON, without a separate UI.
- [Retrieval dataset draft and review packet](../../evaluation/datasets/saleor-retrieval-v0.1/README.md) — real documentation passages, synthetic graph cases and explicit coverage gaps; human approval remains pending. The vector baseline has been scored.
- [Evaluate your own pipeline](../../docs/simple-evaluation.md) — score exported results with JSON field mappings, or add a thin Python adapter without changing ingestion code.
- [Retrieval experiment history](../../docs/retrieval-experiment-history.md) — completed vector, reranker and selector tests, preserved failures and reports, and remaining validation experiments.
- [Use and extend retrieval stages](../../docs/retrieval.md) — implemented Retriever → Reranker → EvidenceSelector pipeline, JSON configuration, failure behavior and replay experiments.

Add another application through its own project configuration. Add a new implementation through the relevant interface and registry. New stages such as browser exploration need their own design; this release does not claim universal repository/framework support.

## Provider selection

LLM reranking can have its own key, model and endpoint in
[retrieval.llm.json](../../configs/retrieval/llm.json), using `ENCODER_API_KEY` from `.env`.
An [OpenAI-compatible API template](../../configs/retrieval/compatible-example.json) lets
you evaluate cheaper providers without editing pipeline code. See the
[configuration and cost controls](../../docs/retrieval.md#separate-llm-model-endpoint-and-key-configuration).
The existing Gemini configuration and local cross-encoder remain available.

The Saleor JSON selects Gemini 3.5 Flash-Lite for requirement extraction and Gemini Embedding 2 (768 dimensions) for retrieval. Each stage has an explicit `provider` and `model` object in [the project JSON](../../configs/ingestion/saleor/project.json). OpenAI remains selectable through the same configuration. Credentials stay in `.env`; for the current selection add `GEMINI_API_KEY`.

See [provider configuration and quota handling](../../docs/ingestion.md#choose-the-provider-directly-in-json). Gemini SDK requests, structured output, embedding batching and failure recovery are verified with mock HTTP responses. After earlier Flash 3.7/3.8 service errors, Flash-Lite passed representative checks, the fully live integration test, and the complete 121-chunk Saleor run. All existing embeddings were reused. Exact quote grounding is verified; no exhaustive semantic-quality evaluation is claimed.

## Repositories and experiment

- Application upstream: https://github.com/saleor/storefront
- Application fork: https://github.com/Maniteja-ai/storefront
- Real change: https://github.com/saleor/storefront/pull/1199
- Baseline branch: `assignment/baseline-pr-1199`, commit `23bc49ccd22e13e182b30daff562d5e5c9af874c`
- Patched branch: `assignment/patched-pr-1199`, commit `221be2247f5b1a8ef94f007639fe83f53a6384b8`

The baseline is the immediate parent of the merged fix. GitHub's original PR base (`6eb0b97b25bd4344d8139515a1cabf763d703b39`) precedes an unrelated CODEOWNERS change. Using the immediate parent isolates exactly the two checkout files changed by the fix.

## Preparation files

- [Environment manifest](../../examples/saleor/environment.json): exact revisions, endpoint, deployment state.
- [Requirements](../../examples/saleor/reference-requirements.json): manually curated reference requirements, source links, and acceptance checks.
- [Setup and evaluation procedure](../../docs/setup.md): deployment and fixture instructions.
- [Evidence policy](../../docs/evidence-policy.md): source provenance, uncertainty, and evaluation separation.

Requirements are reference data for subsequent ingestion/evaluation, not evidence that a crawler discovered anything. Coverage starts at `NOT_EVALUATED`; manual setup smoke checks are recorded separately.

## Live experiment

- [Baseline storefront](https://testsigma-saleor-baseline.vercel.app)
- [Patched storefront](https://testsigma-saleor-patched.vercel.app)
- [Manual validation and evidence](../../docs/validation.md)

Both production builds passed on Vercel with Node 22. Each deployment branch contains the same compatibility adjustments (runtime version and isolation of session requests from the prerender queue). The difference between the deployed application trees remains exactly the two checkout files in PR #1199.

Manual testing confirmed that `TSIGMA10` leaves a $16 cart unchanged on the baseline, while the patched storefront applies the real voucher and displays $14.40, matching Saleor's API. Removal restores $16. These checks demonstrate a usable experiment; they do not replace the assignment's future autonomous exploration and impact analysis.

## Reproduce source comparison

```sh
git clone https://github.com/Maniteja-ai/storefront.git
cd storefront
git diff 23bc49ccd22e13e182b30daff562d5e5c9af874c 221be2247f5b1a8ef94f007639fe83f53a6384b8 --stat
```

Preserve the upstream storefront's license. This preparation repository does not relicense Saleor code or documentation.
