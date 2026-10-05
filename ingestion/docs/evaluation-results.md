# Evaluation results — 2 October 2026

The runnable checks for the implemented ingestion and retrieval stages are complete.
The full assignment evaluation is still incomplete: combined answer generation,
semantic judges, real UI mappings and independently reviewed gold are missing.
Folder names and the frozen dataset were left unchanged.

## Current results

| Check | Result | Scope |
| --- | --- | --- |
| Offline suite | **250 passed**, 6 external cases deselected | Ingestion, recovery, configuration, adapters, retrieval, optional rerankers, static code analyzer, evaluator |
| Live integration | **6 distinct checks passed** | Gemini extraction/embeddings, local persistent Qdrant, real Neo4j, rollback and conflicting concurrent publications |
| Graph retrieval | **20/20 cases matched**; synthetic UI/flow/requirement precision and recall **100%** | Actual Aura queries on a synthetic graph; witness paths, scope and application statuses checked |
| Vector retrieval | **40/40 queries executed**; top-five precision **23.43%**, passage recall **93.18%** | Fresh local Qdrant queries, 30 frozen passages, cached Gemini embeddings, no reranker |
| Evidence coverage | **100% mean required-evidence-unit recall at five** | 35 answerable questions; alternative passages may satisfy the same evidence unit |
| Isolation | **2/2 passed** | Identical-text decoys in another project and another snapshot were excluded |
| Repeated graph query | **100/100 completed and matched**; one distinct output | Fresh Neo4j retrieval of G02; mean 0.612 s, maximum 0.878 s |
| Optional reranker history | **12 saved-output reports rescored unchanged** | Gemini and local cross-encoder; no new model calls or latency claim |
| Dataset integrity | Passed | Frozen input, source and label hashes; labels remain unapproved development references |
| Ruff | Passed | Source, tests and evaluation examples |

Combined statement-and-branch coverage is **91.22%** across the two Python packages:
the then-current packages `trace_impact` **90.69%**, `trace_eval` **95.28%** (before consolidation). Live checks and the Node compiler
are not included in that coverage figure. Test coverage is not semantic accuracy.

The vector scores include 41 relevant returned passages, 134 irrelevant returned
passages and three missing relevant passages across the 35 answerable cases.
The five unanswerable questions are excluded from ranked relevance averages.
Plain vector search returns nearby context; it does not decide whether a question
has an answer. No precision release gate was configured or passed.

The repeated-run result covers one deterministic synthetic graph input, not 100
independent scenarios or 100 complete agent/LLM runs. It establishes observed
consistency for that experiment, not general reliability.

## Fault checks and repairs

The first live batch had three passes and one failure before database access:
the standalone Neo4j test did not load `.env`. That setup was fixed, the failed
case passed on rerun, and the original failure remains in the archive.

Qdrant now rejects wrong-dimensional, zero and nonfinite query vectors before
calling the database. It also verifies every returned payload's project, snapshot
and embedding profile before converting it to a search result. This prevents a
backend that ignores its filters from leaking evidence across scopes.

| Frozen fault contract | Observed result |
| --- | --- |
| F01 Neo4j connection timeout | Error propagates; never a successful empty neighborhood |
| F02 timeout after partial rows | Partial rows are discarded and an error propagates; proposed `PARTIAL_RESULT` reporting is **not implemented** |
| F03 Qdrant unavailable | Error propagates; never treated as an unanswerable question |
| F04 embedding 429 | Retry budgets 0/1/2 produce 1/2/3 attempts; index remains partial and no fabricated embedding is cached |
| F05 dimension mismatch | Rejected before querying Qdrant |
| F06 changed source hash | Dataset validator refuses the corrupt reference |
| F07 wrong project/snapshot/profile | Rejected before scope metadata is discarded |
| F08 judge timeout | **Not executable:** no semantic judge exists yet |

Six safety invariants pass; F02 is partially implemented and F08 is missing.
Most adapters communicate faults through exceptions, not the proposed uppercase
status enums in the design. F04 uses `Retry-After: 0`; nonzero server-delay behavior
was not measured. These are not eight fully implemented status contracts.

The offline run emitted a Google SDK deprecation warning and one SQLite resource
warning. A separate minimal reproduction attributes the latter to Qdrant client
1.19.1's temporary SQLite connection even after explicitly closing its client.
The installed dependency was not modified; the diagnostic is retained.

## What still cannot receive a passing evaluation

All five combined cases were audited using separately retrieved graph and document
outputs. C01/C02 have the required document evidence; C03's graph stays `UNMAPPED`.
C04/C05 have no relevant documentary answer in the bounded corpus. These audits do
**not** execute a combined pipeline or validate generated claims. All five remain
`NOT_IMPLEMENTED` for end-to-end evaluation.

The remaining work is:

- Implement combined graph/document orchestration and test its uncertainty and claim handling.
- Independently review requirement gold across all 121 chunks, including negative chunks, before computing extraction precision/recall.
- Supply reviewed real-code relationships and code-to-UI mappings; then evaluate real impact accuracy, UI exploration and the final PR report.
- Implement and calibrate any semantic judge before running F08.
- Add held-out data and a separately bounded full-agent/model stability experiment.

The 62 quote-grounded ingestion candidates are not a precision score. Synthetic
graph success does not establish that the real Saleor UI mappings are correct.

## Evidence and rerunning

The [campaign summary](../evaluation/history/remaining-evals-2026-10-02/summary.json),
[fault details](../evaluation/history/remaining-evals-2026-10-02/failure-contract-status.json),
[combined audit](../evaluation/history/remaining-evals-2026-10-02/combined-audit.json)
and [experiment history](../evaluation/history/experiments.json) preserve the results.
The archive includes the initial failure, rerun, JUnit, coverage, predictions and hashes.
All 299 previously registered archive hashes were verified before adding this campaign.

```powershell
.venv\Scripts\python.exe -m pytest -m "not integration" -q
$env:RUN_NEO4J_INTEGRATION = "1"
$env:RUN_LIVE_INGESTION = "1"
.venv\Scripts\python.exe -m pytest -m integration -v
.venv\Scripts\python.exe -m trace_impact.evals.runners.graph_benchmark
.venv\Scripts\python.exe -m trace_impact.evals.runners.graph_stability configs/evals/graph-stability.json
```

The stability JSON selects the fixture dataset, case and repetition count (1–100).
Live ingestion tests invoke Gemini and may consume API quota. Graph experiments
write unique synthetic namespaces and do not clear the shared database.
