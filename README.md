# Trace Impact — requirements ingestion for UI and code impact analysis

An application-configured foundation for an agent that connects documented requirements, observed UI, and source code to explain a real pull request's impact. Saleor is the first integration, not a hardcoded dependency.

Implemented: project contracts, public HTML/Markdown collection, versioned snapshots, section chunking, injected application services and ports, a LangChain structured-output adapter, atomic checkpoint storage and run locking, citation validation, a review queue, and a Neo4j schema/loader. **Live LLM extraction and Neo4j connectivity are pending credentials.** The autonomous crawler, UI/code mappings, and PR impact analyzer remain to be implemented. The earlier “steps 1–4” referred to environment preparation, not completion of all four assignment capabilities.

## Start ingestion

```sh
uv sync --locked --extra openai
uv run trace-impact validate-project projects/saleor/project.json
uv run trace-impact collect projects/saleor/project.json
```

The collection stage works without database or LLM credentials. All nine configured Saleor sources were collected successfully. See the [source inventory](artifacts/ingestion/source-inventory.json).

- [Low-level design: classes, patterns, recovery, and release gates](docs/low-level-design.md)
- [Modular setup, Neo4j connection and extraction commands](docs/ingestion.md)
- [Graph schema, absence model and future UI/code links](docs/graph-schema.md)
- [Saleor project configuration](projects/saleor/project.json)
- [Synthetic second integration/template](projects/example/project.json)

To add another application, provide its documentation sources, repository and deployed baseline commit, application URL, and scope through a separate project configuration. HTML/Markdown source adapters are implemented; unsupported formats need an adapter. This is not a claim of universal repository/framework support.

Copy `.env.example` to a local `.env` for Neo4j and model credentials. Never commit that file. Full source snapshots and extraction runs stay under ignored `runs/`. See the setup guide for the remaining live checks.

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
