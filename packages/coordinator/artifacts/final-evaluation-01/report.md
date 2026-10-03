# Final evaluation campaign

Status: **PASSED_WITH_LIMITATIONS**

Campaign: `saleor-final-evaluation-v1`

Passed 17 of 17 machine checks.

| Stage | Check | Result | Measured | Threshold |
| --- | --- | --- | --- | --- |
| ingestion | pipeline completed | PASS | `"COMPLETE"` | `null` |
| ingestion | all chunks indexed and processed | PASS | `{"chunks": 121, "indexed": 121, "processed": 121}` | `null` |
| ingestion | persisted stores verified | PASS | `{"neo4j_chunks": 121, "qdrant": "VERIFIED_AGAINST_SOURCES_AND_CACHE", "source_hashes": "VERIFIED"}` | `null` |
| vector | 40-query benchmark completed | PASS | `40` | `40` |
| vector | passage recall at five | PASS | `0.9318181818181818` | `0.9` |
| vector | required evidence recall at five | PASS | `1.0` | `1.0` |
| vector | mean reciprocal rank | PASS | `0.8952380952380952` | `0.85` |
| optional reranker | selected evidence precision | PASS | `0.9777777777777777` | `0.95` |
| optional reranker | selected evidence recall | PASS | `1.0` | `0.95` |
| neo4j | UI nodes precision and recall | PASS | `{"precision": 1.0, "recall": 1.0}` | `{"precision": 1.0, "recall": 1.0}` |
| neo4j | flows precision and recall | PASS | `{"precision": 1.0, "recall": 1.0}` | `{"precision": 1.0, "recall": 1.0}` |
| neo4j | requirements precision and recall | PASS | `{"precision": 1.0, "recall": 1.0}` | `{"precision": 1.0, "recall": 1.0}` |
| neo4j | 100-run query stability | PASS | `{"completed": 100, "correct": 100, "distinct_success_outputs": 1, "scheduled": 100}` | `100` |
| coordinator | golden contract cases | PASS | `{"case_pass_rate": 1.0, "cases_passed": 4, "cases_total": 4, "false_negative": 0, "false_positive": 0, "precision": 1.0, "recall": 1.0, "true_positive": 2}` | `1.0` |
| coordinator | 100-run agent stability | PASS | `{"completed": 100, "distinct_behavioral_outputs": 1, "max_seconds": 1.515316, "mean_seconds": 0.517745, "pass_rate": 1.0, "passed": 100, "scheduled": 100}` | `100` |
| llm | live grounding and safety | PASS | `{"case_pass_rate": 1.0, "cases_passed": 4, "cases_total": 4, "grounded_finding_rate": 1.0, "latency_ms_max": 2042.85, "latency_ms_median": 1503.8, "provider_calls": 3, "sensitive_output_leaks": 0, "structured_output_rate": 1.0}` | `1.0` |
| end to end | live PR workflow and behavioral verification | PASS | `{"attestation": "VERIFIED", "attribution": "SUPPORTED", "completeness": "COMPLETE_FOR_CONFIGURED_SCOPE", "status": "COMPLETED", "verification": "COMPLETED"}` | `null` |

## Limits on the claim

- Requirement extraction has quote-grounding checks, but no independently reviewed semantic precision/recall over all 121 chunks.
- Retrieval and graph labels are development references created by the implementation author; no independent reviewer approved them.
- Raw vector precision at five is 23.43%; use the evidence selector when precision matters.
- Neo4j accuracy uses a synthetic contract graph; the Saleor code-to-UI-to-requirement graph is not independently labelled.
- Coordinator accuracy covers 4 fixture cases; broader PR coverage remains future work.
- Live LLM evaluation has four focused cases and three provider calls; it is a safety/grounding check, not a statistical model-quality claim.
- PR number and replay status are configuration labels; remote PR metadata was not verified.
- UI links have static structural validation and an attested deployed revision; behavior is reported separately.
- Only the explicitly listed behavioral checks were run; unlisted flows are outside scope.
- Reviewed requirement contracts passed for this scenario; other retrieved requirements remain unvalidated.

## Interpretation

The measured pipeline contracts pass for the configured Saleor voucher scenario. The limitations above prevent a claim of general production accuracy across arbitrary repositories and PRs.
