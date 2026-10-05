# Retrieval experiment history

This is our record of retrieval tests and the next experiments toward `Retriever → Reranker → EvidenceSelector`. Keep each measured result so we can see whether a change improves precision without losing the evidence needed to answer a question.

The dense baseline, Gemini reranking and threshold-based evidence selection have now been implemented and tested. The machine-readable [experiment history](../evaluation/history/experiments.json) includes completed measurements, a failed request-schema attempt and remaining experiments, with a [JSON Schema](../configs/evals/validation_schema/experiment-history.schema.json) for editor completion and validation. See [the implementation guide](retrieval.md) for configuration and extension examples.

## Remaining evaluation campaign

[Current evaluation results](evaluation-results.md) record 250 offline passes, six
live integration passes, fresh graph/vector benchmarks, a 100-repeat graph check,
and fault coverage. Optional reranker outputs were rescored without additional model
calls. The campaign is marked `INCOMPLETE` overall because combined orchestration,
semantic judges and independent gold are still missing. Earlier evidence is preserved.

## Neo4j graph retrieval and vector default

Vector retrieval now defaults to returning five passages through the API and CLI.
Reranking remains an explicit JSON choice. This is a cost/simplicity decision, not a
claim that vector ranking has higher precision than the previously measured Gemini
reranker. The existing measurements remain unchanged.

The new graph benchmark ran 20 frozen synthetic cases against real Neo4j. All 20
completed with the expected status. UI sets (32 matches), flow sets (32 matches) and
requirement sets (22 matches) each had zero false positives and zero false negatives:
**100% precision and recall for this synthetic contract**. Nine cases had empty
reference sets; these were included in set scoring and status validation. Returned
witnesses passed direction, project/revision, confirmed-edge, hop-budget and acyclicity
checks. Repeated publication preserved counts; immutable-ID content changes were rejected.

The [final graph report](../evaluation/history/neo4j-graph-2026-10-02/summary.json),
[predictions and witnesses](../evaluation/history/neo4j-graph-2026-10-02/predictions.jsonl)
and [timings](../evaluation/history/neo4j-graph-2026-10-02/analysis.json) preserve the evidence.
The initial run is archived separately; both runs reused the same 20 cases and do not
represent 40 distinct tests or independent validation datasets. No production impact
accuracy or combined graph-plus-vector quality is established.

The real [Saleor code graph smoke](../evaluation/history/saleor-code-graph-2026-10-02/summary.json)
published 2,101 nodes and 5,365 relationships from the pinned deployed baseline.
Querying the two PR files returned 43 symbols across seven files in one observed
0.93-second traversal. Source nodes and edge witnesses are retained in the
[impact artifact](../evaluation/history/saleor-code-graph-2026-10-02/saleor-impact.json).
Its UI status is `UNMAPPED`: real UI/flow mappings have not been discovered or validated.
Unresolved/generated modules and unsupported runtime behavior remain explicit gaps.

**Decision:** keep plain vector retrieval as the default and use the code graph as
scoped static evidence. Prioritize obtaining real UI mappings and independently
reviewing them before running the combined graph/vector cases.

## Local cross-encoder comparison and decision

The dedicated `cross-encoder/ms-marco-MiniLM-L6-v2` implementation completed the same 40 questions using the same saved ten candidates per question. Model revision: `233902d25c440f23af6f7d6e94d2946bac0bee0a`. The raw-logit selection cutoff was declared as zero before execution; no labels or threshold fitting were used by the pipeline.

| Metric | Gemini reranker + selector | MiniLM cross-encoder + selector |
| --- | ---: | ---: |
| Precision at 1, 35 answerable queries | 97.14% | 77.14% |
| Selected-set precision, all 40 queries | 97.78% | 53.45% |
| Selected relevant-passage recall | 100.00% | 70.45% |
| Required evidence recall, answerable queries | 100.00% | 75.71% |
| Correct empty selections on unanswerable queries | 5/5 | 5/5 |
| Incorrect empty selections on answerable queries | 0 | 6 |

MiniLM returned 58 passages: 31 relevant, 27 extra, with 13 relevant passages missed under the frozen labels. It also ranks the first result worse than the original dense baseline (82.86% Precision@1). Keeping top two improves evidence recall to 94.29%, but does not match Gemini's 100%. The [error analysis](../evaluation/history/cross-encoder-2026-10-02/analysis.json) includes the incorrect and missed IDs for every affected query.

**Decision: retain the cross-encoder as an optional local implementation and keep `retrieval.gemini.json` as the recommended quality configuration.** A dedicated cross-encoder does not automatically improve this corpus. These results apply to this model, threshold and development dataset, not all cross-encoders. Future model selection and threshold calibration need separate validation data.

Warm-model reranking averaged 0.433 seconds per question on this CPU, with 8.58 seconds for first construction using already downloaded weights. There were 400 scored windows and no external reranking calls. This is a local sample, not a load test or a controlled speed comparison with Gemini's paced API requests. The indexed smoke overlapped part of the benchmark. Initial model download time is excluded.

The [archived summary](../evaluation/history/cross-encoder-2026-10-02/summary.json), [model manifest](../evaluation/history/cross-encoder-2026-10-02/model-manifest.json) and query artifacts preserve the exact model, dependency versions, hashes and scores. The [full-index smoke](../evaluation/history/cross-encoder-indexed-smoke-2026-10-02/summary.json) independently ran one live embedding/Qdrant query over the 121-chunk ingestion run, returning ten candidates and scoring fourteen windows using cached model weights. Its five selected passages were not gold-scored.

Preserved failures: an initial smoke exposed the removed `prepare_for_model` API in Transformers 5.18.0. The replacement overflow API then failed a long-document test with tokenizers 0.23.2: only 64 of 201 tokens were reconstructed. That stack was rejected, and its [attempt archive](../evaluation/history/cross-encoder-compatibility-attempt-2026-10-02/validation-failure.json) remains visible. The pinned Transformers 4.57.6/tokenizers 0.22.2 stack passed full-token coverage tests and a fresh 40-question benchmark. It produced the same short-passage rankings, but is the validated implementation.

## Original vector baseline

On 2 October 2026, the existing Gemini embedding adapter and Qdrant search adapter processed 40 queries against 30 frozen document passages. Two additional identical-text documents tested project and snapshot isolation. The model was `gemini-embedding-2`, using 768 dimensions and the existing search formatting. Qdrant ran locally in an isolated persistent directory.

The five rows below score the same saved rankings. They are one live retrieval run with five result cutoffs, not five independent model runs. Top 1 and top 2 were scored later without additional model calls.

| Returned passages | Precision | Relevant passage recall | Required evidence recall | Saved report |
| --- | ---: | ---: | ---: | --- |
| Top 1 | 82.86% | 65.91% | 80.00% | [Report](../evaluation/history/vector-baseline-2026-10-02/report-k1.json) |
| Top 2 | 47.14% | 75.00% | 88.57% | [Report](../evaluation/history/vector-baseline-2026-10-02/report-k2.json) |
| Top 3 | 33.33% | 79.55% | 92.86% | [Report](../evaluation/history/vector-baseline-2026-10-02/report-k3.json) |
| Top 5 | 23.43% | 93.18% | 100.00% | [Report](../evaluation/history/vector-baseline-2026-10-02/report-k5.json) |
| Top 10 | 12.57% | 100.00% | 100.00% | [Report](../evaluation/history/vector-baseline-2026-10-02/report-k10.json) |

Ranking metrics cover 35 answerable questions. The five unanswerable questions completed retrieval but are excluded from those metrics. We have not tested whether an answering system correctly refuses unsupported questions. All labels remain draft and all questions belong to the development split; these results do not establish production accuracy.

Precision counts directly supporting passages as relevant. Related background passages do not earn a relevant-passage match. Required-evidence recall counts whether at least one acceptable passage supplies each required fact. This explains why top 5 reaches 100% evidence recall while missing three alternative supporting passages.

There are only 44 relevant passage judgments across the 35 answerable questions. Always returning five creates 175 result slots, so the maximum possible Precision@5 on this dataset is 44/175, or 25.14%. The measured result was 41/175. Selecting fewer passages is a useful experiment, but its success must also preserve multi-part answer evidence.

## Execution checks and failures

The following checks describe the original vector baseline. The later reranker run is recorded separately below.

| Check | Observed result |
| --- | --- |
| Dataset integrity validator | Passed before the live run; approval remained false |
| Live query execution | 40 of 40 completed; no Gemini errors surfaced |
| Application embedding calls | 43: three document batches and 40 queries; SDK attempts are not separately counted |
| Qdrant write and reopen verification | All 32 records verified, including the two decoys |
| Other project document | Retrievable in its own scope; excluded from the target scope |
| Old snapshot document | Retrievable in its own scope; excluded from the target scope |
| Evaluator and dataset unit checks | 23 tests passed during this benchmark work |
| Benchmark script lint | Passed |
| Initial local storage error | SQLite could not open the long database path; resumed with a shorter isolated path and reused cached passage embeddings |
| Latency and monetary cost | Not measured; do not infer them from the request count |
| Neo4j retrieval, code impact, semantic answers and UI validation | Not exercised by this benchmark |

The [archived predictions](../evaluation/history/vector-baseline-2026-10-02/predictions.jsonl), [execution record](../evaluation/history/vector-baseline-2026-10-02/retrieval-run.json), [isolation checks](../evaluation/history/vector-baseline-2026-10-02/isolation.json) and [miss analysis](../evaluation/history/vector-baseline-2026-10-02/analysis.json) preserve the evidence. They are outside ignored temporary run directories and can be versioned with the project. Original reports retain their original absolute execution paths; the links in this history point to the portable copies. This archive does not include credentials, embedding caches or database files.

## Findings to preserve

Top 1 gives better precision but misses 20% of required evidence on average. Top 3 misses evidence for questions V24, V27 and V32: an API error field, replacement API arguments, and a combined delivery and shipping-voucher question. Top 5 covers all required evidence in the draft references, so it remains our coverage baseline.

We will not call a change successful merely because returning fewer passages raises precision. We must also measure the evidence lost, especially for questions requiring more than one passage.

## Completed reranker and selector experiment

The successful run reused the same ten vector candidates per question and made 40 live Gemini reranking calls. It made no new embedding or database retrieval calls. The reranker-only and selector reports share that one execution; they are not two independent model runs.

| Comparable ranking metric | Dense baseline | Gemini reranking |
| --- | ---: | ---: |
| Precision at 1 | 82.86% | 97.14% |
| Required evidence recall at 1 | 80.00% | 92.86% |
| Precision at 2 | 47.14% | 61.43% |
| Required evidence recall at 2 | 88.57% | 100.00% |
| Precision at 5 | 23.43% | 25.14% |
| Required evidence recall at 5 | 100.00% | 100.00% |

The separate selector kept grade-3 passages, deduplicated exact text and capped results at five. Across all 40 questions it returned 45 passages: 44 relevant under the draft labels, one extra and no missed relevant passages. Selected-set precision was **97.78%**, passage recall **100%**, and required-evidence recall across the 35 answerable questions **100%**. All five unanswerable questions received empty selections, with no empty selections on answerable questions. The average final count was 1.125 passages across all questions, including empty results.

Selected-set precision divides by actual returned passages; fixed-cutoff precision divides by the configured result slots. These are distinct measurements. The same-cutoff table above shows the ranking improvement independently of returning fewer passages.

The one extra passage was P22 for V24, a question asking for both the add-voucher mutation and its current error field. P22 names the mutation but does not supply the error field; P27 was also selected and supplies the expected evidence. P22 remains an extra under the frozen labels. Human review should resolve whether partial support and redundant evidence are labeled as intended; no label was changed to improve the score.

See the [reranking report](../evaluation/history/gemini-reranking-2026-10-02/reranked-k1-report.json), [selection report](../evaluation/history/gemini-reranking-2026-10-02/selected-report.json), [per-query execution data](../evaluation/history/gemini-reranking-2026-10-02/execution.json) and [analysis](../evaluation/history/gemini-reranking-2026-10-02/analysis.json). Individual query artifacts preserve candidate passages, grades, quotes, final selections and status.

The initial request schema failed with HTTP 400 and was stopped after three failed questions. A simplified provider schema succeeded, while strict local limits and validation remained enabled. The [failed attempt](../evaluation/history/reranker-schema-attempt-2026-10-02/execution.json) remains in history. The successful 40-query run completed without errors. Its provider-reported usage was 69,465 input tokens and 10,732 output tokens; no monetary cost was measured. Mean reranking time was 11.74 seconds including the five-requests-per-minute pacing delay, so this is not pure model latency.

Final verification passed **180 offline tests**, with four live-marked tests deselected, **92.65% branch-inclusive coverage** for `trace_impact`, clean lint and a successful wheel build. The new retrieval test file contains 35 passing tests, including a real local Qdrant plus mocked-SDK end-to-end test and nested custom-plugin schema tests. The separate 40-query live experiment verifies real provider behavior; none of these results alone certifies production readiness.

Decision: retain the reranker and selector for further validation. The current evidence is a small draft development dataset. Hybrid retrieval, independently reviewed held-out evaluation and generated-answer evaluation remain outstanding.

A final [live indexed-corpus smoke check](../evaluation/history/indexed-corpus-smoke-2026-10-02/summary.json) called the public API against the existing 121-chunk ingestion index. One real query passed through Gemini embedding, Qdrant search, Gemini reranking and selection: ten candidates produced two selected passages with validated source quotes. Retrieval took 1.74 seconds and reranking 8.52 seconds for that one call. This verifies the integration path; it was not scored against gold labels and does not extend the 30-passage benchmark accuracy claim.

## Implemented stage responsibilities

| Stage | Simple responsibility | Replaceable interface shape |
| --- | --- | --- |
| Retriever | Find candidate passages within the requested project and snapshot | `retrieve(query, scope, limit) -> candidates` |
| Reranker | Reorder candidates using the question together with each passage | `rerank(query, candidates) -> ranked_candidates` |
| EvidenceSelector | Select qualified evidence and return empty when nothing qualifies | `select(query, candidates, ranking) -> selection` |

Each stage preserves passage IDs, source references and scope. A reranker returns scores for existing IDs; the service validates and orders them. A selector returns a subset with explicit selection status. Rerankers and selectors are chosen through registered JSON providers. A different Retriever can be injected through the library; the CLI uses the indexed-corpus retriever. `EVIDENCE_FOUND` does not claim full semantic coverage, which remains `NOT_EVALUATED` at runtime.

## Experiment sequence

| Experiment | Change to test | Main comparison | Status |
| --- | --- | --- | --- |
| Reranker | Reorder the same ten dense candidates | Precision and evidence recall at unchanged cutoffs versus the baseline | Completed provisionally |
| Evidence selector | Select a variable number of reranked passages and return empty when none qualify | Actual returned-set precision, evidence recall, average result count and empty-selection errors | Completed provisionally |
| Hybrid retriever | Combine keyword and vector candidates for exact identifiers | Dense versus hybrid retrieval with the same downstream stages | Planned |
| Independent validation | Freeze the chosen configuration and use independently reviewed, unused questions | Whether improvements generalize beyond development data | Planned |

Reranking and selection were measured separately from the same execution. The selected example uses Gemini 3.5 Flash-Lite with grade 3 as the relevance cutoff. Hybrid retrieval remains a separate hypothesis; independent label review and held-out evaluation should precede a production quality claim.

For variable output counts, report precision using the actual number of passages returned. Continue reporting fixed-cutoff ranking metrics separately. Preserve evidence-recall and answerable-query denominators, and record false abstentions so an empty result cannot appear to be a perfect system. Similarity scores are not calibrated probabilities; thresholds require development testing.

## How to add the next result

1. State the hypothesis and change one stage at a time. Record the baseline experiment, dataset version and split before running.
2. Save the exact stage configuration, code revision or dirty-state snapshot, model versions and input hashes. Keep labels out of retrieval and selection inputs.
3. Save ordered candidates, reranker scores, selected evidence IDs and selection status. Record query failures as failures, not empty successful results.
4. Score against the same frozen references for a direct comparison. Measure latency, result count and provider usage for future runs; mark unmeasured fields as unknown.
5. Add a new completed or failed record to `evaluation/history/experiments.json`, with copied reports and file hashes in a new evidence directory. Keep earlier records and reports unchanged. Move a planned item to completed history only when its results exist.
6. Add a row here explaining the change, metrics, regressions and decision: keep, revise or reject. After tuning, use separately reviewed held-out questions for the final check.

History recording is currently explicit JSON and documentation maintenance; it is not automatic experiment tracking. The schema validates the record shape, not the truth of results or immutable storage. Git can preserve revisions once these files are committed; no commit or push was made as part of recording this history.

To reproduce the existing live benchmark or rescore exported predictions, follow [the evaluation guide](simple-evaluation.md). Review the gold labels before treating any experiment as a release gate.
