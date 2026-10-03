# Real-PR ground-truth dataset

This directory is the coordinator's real-PR evaluation set. It currently contains six frozen
Saleor Storefront pull requests: four development cases and two held-out cases.

## Files

| File | Meaning |
| --- | --- |
| `dataset.json` | Expected UI elements, user flows, requirements, claim IDs, allowed evidence and review provenance |
| `sources/pr-<number>.json` | Frozen GitHub PR metadata and patches used by the reviewer; each file is SHA-256 bound from `dataset.json` |
| `candidate-predictions.json` | A scorer smoke fixture authored with the expected labels visible; it is not an agent-accuracy result |
| `../../schemas/real-pr-evaluation.schema.json` | JSON Schema for editor validation |
| `../../artifacts/real-pr-evaluation-01/report.json` | Latest machine-readable score |

The source snapshots are immutable evaluation evidence. If a source file changes, its stored hash
no longer matches and evaluation stops.

## What ground truth means

For each PR, a reviewer reads the frozen diff and records:

- UI elements that a user can see or operate;
- user flows that can change;
- requirements that should still hold;
- individual factual claims and the exact diff evidence allowed to support each claim.

The labels become approved ground truth only after a second person reviews every case, checks the
held-out split, sets `review.status` to `APPROVED`, and records their different name and review date.
The schema rejects self-approval. The current dataset remains `DRAFT`, so its result cannot pass a
release gate.

## Metrics

Relevance is calculated over three exact-label sets: UI elements, flows and requirements.

```text
precision = true positives / (true positives + false positives)
recall    = true positives / (true positives + false negatives)
```

The evaluator reports each dimension and a combined micro score. A no-impact PR is included so an
agent that always predicts an impact receives false positives.

Citation validity asks whether every cited evidence ID exists in that PR's frozen source. It does
not prove that the evidence supports the claim.

Faithfulness is stricter. A predicted claim is faithful only when its claim ID exists in the
reviewed reference and at least one cited evidence ID belongs to that claim's reviewer-approved
support set. This deterministic check avoids an LLM judging another LLM.

Claim recall measures how many expected claims were returned faithfully. It prevents a system from
getting perfect faithfulness by making only one safe claim.

## Run the scorer

From `packages/coordinator`:

```powershell
uv run --no-sync trace-coordinator evaluate-real-prs `
  evaluation/real-pr-v2/dataset.json `
  evaluation/real-pr-v2/candidate-predictions.json `
  --output artifacts/real-pr-evaluation-01
```

The candidate predictions currently score 1.0 because they deliberately mirror the draft labels
to test the scorer. Their provenance says `labels_visible: true`, which makes them ineligible for a
release decision even after dataset approval. A real evaluation must export predictions from the
coordinator without exposing held-out labels and set provenance to `kind: system_run` and
`labels_visible: false`.

## Independent review checklist

1. Open every frozen source and read the changed hunks without looking at candidate predictions.
2. Confirm each UI element, flow and requirement; add missing labels and remove unsupported ones.
3. Confirm every claim and its allowed evidence IDs.
4. Ensure the two held-out cases were not used for prompt, retriever or threshold tuning.
5. Put the reviewer's name and date in `dataset.json`, set status to `APPROVED`, and rerun tests.
6. Generate fresh blind system predictions; never promote `candidate-predictions.json`.
