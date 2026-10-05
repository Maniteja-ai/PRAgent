# Ingestion evaluation data

This folder contains the draft retrieval dataset referenced by `configs/saleor/evaluation.json`. It is input and reference material, not a standalone scoring command. There is currently **no ingestion retrieval scorer** that calculates precision, recall, or faithfulness for this dataset; stage execution is recorded separately by the configured evaluation decorator.

## Run ingestion and its tests

From the project root, set up and run the ingestion component as described in the [ingestion README](../README.md):

```powershell
cd ingestion
uv sync --group dev
uv run pytest -q
```

To execute the configured pipeline against the Saleor sources and stores, run `uv run ingest configs/saleor.json`. This may call Gemini and write to Qdrant and Neo4j. Use `--code-ui-only` for a code/route refresh without embedding calls. See the [dataset README](datasets/saleor-retrieval-v0.1/README.md) for cases, labels, provenance and review status.

## What is evaluated today

- The dataset bundle is `datasets/saleor-retrieval-v0.1/`; its manifest includes counts, schemas, hashes, and boundaries between target inputs and scorer-only labels.
- Vector and graph examples are drafts. The graph cases use a synthetic fixture; they do not certify the real Saleor graph or UI mappings.
- Cases remain `DRAFT_PENDING_HUMAN_REVIEW`; there are no held-out cases. Do not present draft scores as a validated benchmark.
- The separate agent report-quality and code-graph evaluation commands are documented under [`agents/evaluation/`](../../agents/evaluation/).
