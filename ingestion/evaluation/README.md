# Saleor evaluation data

`evaluation.json` points to `datasets/saleor-retrieval-v0.1/manifest.json`. The manifest identifies
one versioned dataset bundle; each file under that folder has a defined role and frozen hashes.

## What is already here

- 40 vector retrieval cases with graded relevance labels and 30 frozen passages.
- 20 Neo4j retrieval cases over a synthetic graph fixture.
- Combined, isolation and failure-contract cases, plus source snapshots and a human review packet.

This is a useful starting dataset for retrieval. It is **not an approved golden release**: the
manifest is `DRAFT_PENDING_HUMAN_REVIEW`, all cases are development cases, and there are no held-out
cases. The graph suite checks the retrieval contract on synthetic relationships; it does not prove
real Saleor code-to-UI mapping quality.

The manually prepared [Saleor requirements](../examples/saleor/reference-requirements.json) are
separate reference material. They are not ground-truth labels yet because some acceptance statements
are inferred from backend documentation and still need review against the storefront.

## Current project capability

The `@record_stage` decorator records stage status, duration and result hashes. The current project
has not yet implemented a dataset validator or a scoring runner. Therefore, `dataset_manifest` keeps
this draft dataset discoverable in configuration, but no precision, recall, grounding or
faithfulness scores are produced yet. Review the dataset, then add a validator and offline scorer
before reporting quality metrics.

See [the dataset review guide](datasets/saleor-retrieval-v0.1/README.md) for case formats and review
instructions.

