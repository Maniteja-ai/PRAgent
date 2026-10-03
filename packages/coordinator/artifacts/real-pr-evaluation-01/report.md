# Real-PR relevance and faithfulness evaluation

Status: **DRAFT_EVALUATED**

Release eligible: **false**

Dataset: `saleor-real-pr-v2-draft` with 6 cases (2 held out).

Prediction source: `candidate_manual`; labels visible: **true**.

| Metric | Result |
| --- | ---: |
| Combined relevance precision | 1.000 |
| Combined relevance recall | 1.000 |
| Faithfulness | 1.000 |
| Claim recall | 1.000 |
| Citation validity | 1.000 |

## Review status

Label author: Codex-assisted implementation

Independent reviewer: PENDING

- DRAFT_EVALUATED is development feedback, not independently approved ground truth.
- Predictions produced with labels visible are scorer smoke tests, not model accuracy.
- Faithfulness is reference-based evidence support; it does not use an LLM-as-judge.
