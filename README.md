# Trace Impact — requirements ingestion for UI and code impact analysis

An application-configured foundation for an agent that connects documented requirements, observed UI, and source code to explain a real pull request's impact. Saleor is the first integration, not a hardcoded dependency.

Implemented: a reusable Python ingestion library with configuration-selected loaders, parsers, chunkers, extraction, embeddings and storage. Neo4j holds requirement/evidence relationships; Qdrant holds searchable chunks and vectors. Original evidence and checkpoints stay in local artifacts.

**Neo4j connectivity and document publication are verified:** nine document snapshots and 121 chunk references, with repeat-load and live integration checks passing. See [verification evidence](artifacts/ingestion/neo4j-verification.json). **Live model extraction/embedding is pending credentials.** Local Qdrant storage/search is tested with labeled test embeddings. Autonomous browser exploration, code analysis, UI/code mapping, full RAG answers and PR impact analysis remain future work. The earlier steps 1-4 referred to environment preparation, not completion of all four assignment capabilities.

## Start with the extension example

```sh
uv sync --locked --extra openai --extra vector
uv run python examples/custom_parser.py
uv run trace-impact components
uv run trace-impact collect projects/saleor/project.json
```

The example shows the library's main idea: implement a parser, register it under a name, and let user configuration select it. It runs without credentials. Collection works the same way for the Saleor README and public documentation. See the [source inventory](artifacts/ingestion/source-inventory.json).

- [Simple LLD: interfaces, flow, storage and extension points](docs/low-level-design.md)
- [Working custom parser](examples/custom_parser.py) and [its user configuration](projects/plugin-example/project.yaml)
- [Setup, extraction, indexing and search commands](docs/ingestion.md)
- [Graph schema and future UI/code relationships](docs/graph-schema.md)
- [Saleor configuration](projects/saleor/project.json)

Add another application through its own project configuration. Add a new implementation through the relevant interface and registry. New stages such as browser exploration need their own design; this release does not claim universal repository/framework support.

## Repositories and experiment

- Application upstream: https://github.com/saleor/storefront
- Application fork: https://github.com/Maniteja-ai/storefront
- Real change: https://github.com/saleor/storefront/pull/1199
- Baseline branch: `assignment/baseline-pr-1199`, commit `23bc49ccd22e13e182b30daff562d5e5c9af874c`
- Patched branch: `assignment/patched-pr-1199`, commit `221be2247f5b1a8ef94f007639fe83f53a6384b8`

The baseline is the immediate parent of the merged fix. GitHub's original PR base (`6eb0b97b25bd4344d8139515a1cabf763d703b39`) precedes an unrelated CODEOWNERS change. Using the immediate parent isolates exactly the two checkout files changed by the fix.

## Preparation files

- [Environment manifest](configs/saleor.json): exact revisions, endpoint, deployment state.
- [Requirements](requirements/saleor.json): manually curated reference requirements, source links, and acceptance checks.
- [Setup and evaluation procedure](docs/setup.md): deployment and fixture instructions.
- [Evidence policy](docs/evidence-policy.md): source provenance, uncertainty, and evaluation separation.

Requirements are reference data for subsequent ingestion/evaluation, not evidence that a crawler discovered anything. Coverage starts at `NOT_EVALUATED`; manual setup smoke checks are recorded separately.

## Live experiment

- [Baseline storefront](https://testsigma-saleor-baseline.vercel.app)
- [Patched storefront](https://testsigma-saleor-patched.vercel.app)
- [Manual validation and evidence](docs/validation.md)

Both production builds passed on Vercel with Node 22. Each deployment branch contains the same compatibility adjustments (runtime version and isolation of session requests from the prerender queue). The difference between the deployed application trees remains exactly the two checkout files in PR #1199.

Manual testing confirmed that `TSIGMA10` leaves a $16 cart unchanged on the baseline, while the patched storefront applies the real voucher and displays $14.40, matching Saleor's API. Removal restores $16. These checks demonstrate a usable experiment; they do not replace the assignment's future autonomous exploration and impact analysis.

## Reproduce source comparison

```sh
git clone https://github.com/Maniteja-ai/storefront.git
cd storefront
git diff 23bc49ccd22e13e182b30daff562d5e5c9af874c 221be2247f5b1a8ef94f007639fe83f53a6384b8 --stat
```

Preserve the upstream storefront's license. This preparation repository does not relicense Saleor code or documentation.
