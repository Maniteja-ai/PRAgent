# Testsigma impact agent: Saleor preparation

Preparation for an agent that connects documented requirements, observed storefront UI, and source code to explain a real pull request's impact.

This repository currently contains the environment manifest, requirements, and reproduction plan for assignment steps 1–4. The autonomous crawler, ingestion pipeline, Neo4j graph, and impact analyzer have **not yet been implemented**.

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

Requirements are reference data for subsequent ingestion/evaluation, not evidence that a crawler discovered anything. Coverage starts at `NOT_EVALUATED`. Deployment and runtime verification remain pending until recorded in the manifest.

## Reproduce source comparison

```sh
git clone https://github.com/Maniteja-ai/storefront.git
cd storefront
git diff 23bc49ccd22e13e182b30daff562d5e5c9af874c 221be2247f5b1a8ef94f007639fe83f53a6384b8 --stat
```

Preserve the upstream storefront's license. This preparation repository does not relicense Saleor code or documentation.

