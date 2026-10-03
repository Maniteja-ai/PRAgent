# Evaluate an existing pipeline with saved results

See the [retrieval experiment history](retrieval-experiment-history.md) for measured baseline, reranking and evidence-selection results. Each completed experiment keeps its own evidence and configuration in the [JSON history](../evaluation/history/experiments.json).

The [retrieval stage guide](retrieval.md) explains the implemented reranker and selector and how to evaluate them on the saved baseline candidates.

The evaluation entry point is independent of ingestion. Supply a reference dataset, exported pipeline results and a JSON config mapping their fields. The evaluator computes retrieval metrics without editing your pipeline or connecting to its databases. This is an initial deterministic scorer, not a claim that every pipeline or semantic task is automatically evaluated.

## Try the local example

From the repository root, after syncing the existing environment:

```powershell
uv run --no-sync trace-eval run examples/evaluation/eval.json
```

The example uses synthetic exported results with an intentional mistake. It should report TP=2, FP=1, FN=1: precision, recall and F1 are each about 66.67%. The third case has an empty reference and empty output, recorded separately as a successful negative set case. The labels are unapproved, so this is a provisional demonstration, not a Saleor benchmark or release pass.

The command prints a summary and the path to a new `report.json` containing per-case matches, mistakes, omissions, unjudged IDs, duplicates, counts and input fingerprints. Reports use unique directories and do not overwrite previous runs.

## Connect your own pipeline in three steps

1. Use or create reference JSONL cases with stable case IDs and independently reviewed expected results.
2. Export your pipeline outputs to JSONL, preserving result rank and returning comparable stable IDs.
3. Point a config at both files, set the field mappings, and run the same command.

Example config:

```json
{
  "$schema": "../../schemas/evals/evaluation.schema.json",
  "dataset": "my-cases.jsonl",
  "predictions": "my-results.jsonl",
  "task": "ranked_retrieval",
  "k": 5,
  "reference": {
    "expected_ids": "reference.expected_ids",
    "complete": true
  },
  "prediction": {
    "case_id": "request_id",
    "items": "response.documents",
    "item_id": "id"
  }
}
```

Paths resolve relative to the config file. A field path such as `response.documents` reads a nested JSON object; it does not execute code or import plugins. `item_id` reads the ID within each result object. Omit `item_id` when results are already strings. There is no wildcard/JSONPath engine; use a thin adapter for formats this mapping cannot express.

`complete: true` is a declaration that expected labels are exhaustive within the case scope. Do not set it merely to obtain a score. With partial references, precision is withheld and the run is incomplete. With a declared candidate universe, out-of-universe results are UNJUDGED; they cannot silently improve or pass a score.

This removes pipeline coupling, not the need for gold data. New repositories require appropriate expected answers, and returned IDs must match the chosen reference identity scheme. Source text or arbitrary natural-language answers need additional matching/evaluation logic; this scorer does not pretend ID matching establishes semantic equivalence.

## Use the retrieval datasets already prepared

The [vector template](../configs/evals/vector-example.json) reads the drafted relevance grades and evidence units. Export one row per case:

```json
{"id":"V01","retrieved_ids":["P23","P16","P24"],"execution_status":"OK"}
```

Use the isolated 30-passage corpus with those passage IDs, not production chunk IDs from the 121-chunk index. Reference judgments remain hidden from the target. Labels are draft, so scores remain provisional. The template does not manufacture predictions: its output file must be supplied by your retrieval pipeline.

The [graph template](../configs/evals/graph-example.json) scores returned flow sets and application statuses:

```json
{"id":"G06","flow_ids":[],"status":"UNMAPPED","execution_status":"OK"}
```

`UNMAPPED` is the application's answer; it is distinct from execution failure. To score UI elements or requirements, use another config mapping `reference.ui_ids`/`reference.requirement_ids` and the corresponding output field. Keep separate scorecards rather than mixing unlike IDs. A pipeline that returns multiple result types can be evaluated from the same saved file.

These templates support vector/code ranked IDs and graph entity sets. The generic scorer does not execute Cypher or verify paths. The separate live graph runner below executes Neo4j retrieval and checks returned witnesses against the input fixture before using the generic scorer. Combined-evidence reasoning and semantic report quality still need separate evaluation. Run the dataset integrity validator before scoring the supplied package.

## Run the live Neo4j contract benchmark

```powershell
uv run --no-sync python -m trace_impact.evals.runners.graph_benchmark
```

This explicitly uses real Neo4j credentials from `.env`. It creates a unique fixture
snapshot containing 29 synthetic nodes and 32 edges, verifies idempotent publication
and rejection of changed contents under the same snapshot ID, then retrieves all 20
graph cases. It makes no model or vector calls and does not clear existing graph data.
Pipeline execution receives only the fixture and input queries. Labels are loaded
after retrieval to score UI IDs, flow IDs and requirement IDs separately, including
application-status mismatches. Results, witnesses, timings, checks and scorer reports
are saved under the printed run directory.

The frozen labels are draft synthetic contracts. A perfect score validates this bounded
topology, not correctness of real Saleor UI mappings, static extraction completeness or
all graph scenarios. The latest measured results are in
[experiment history](retrieval-experiment-history.md#neo4j-graph-retrieval-and-vector-default).

## Run the live vector example

The reusable example selects the embedding provider from the project JSON and uses the existing Qdrant adapter with an isolated local database. The [benchmark config](../configs/evals/vector-benchmark.json) selects the project, input files, label file, output directory and scoring cutoffs. Credentials come from the configured environment file and are not written to reports.

```powershell
uv run --no-sync python -m trace_impact.evals.validation.dataset evaluation/datasets/saleor-retrieval-v0.1
uv run --no-sync python -m trace_impact.evals.runners.vector_benchmark configs/evals/vector-benchmark.json
```

The second command makes live embedding API calls. It embeds 30 passages, indexes two identical-text isolation decoys under other project/snapshot scopes, retrieves the top 10 results for 40 questions, and scores the saved predictions at k=3, 5 and 10. Retrieval receives only input files; the scoring step reads labels afterwards. This is an application boundary rather than a filesystem sandbox.

The printed run directory contains `predictions.jsonl`, per-query score diagnostics, the embedding checkpoint, isolation checks and `summary.json` linking the detailed scoring reports. The local database lives in a separate short per-run directory to avoid Windows SQLite path-length limits. Neither the production vector index nor Neo4j is opened.

To resume after an interruption, repeat the command with `--run-dir` and the printed directory. Successful embeddings are reused only when the input fingerprints and embedding profile match. Failed query embeddings are attempted again; cached queries are searched again without a model call. Each API call has a 45-second timeout and at most two SDK attempts. This example measures raw retrieval on draft labels, not generated-answer correctness or production readiness.

## Optional Python integration

When saved outputs are unavailable, implement one small adapter outside pipeline internals:

```python
from trace_impact.evals import CaseInput, Prediction, evaluate

class MyRetrieverAdapter:
    def __init__(self, retriever):
        self.retriever = retriever

    def predict(self, case: CaseInput) -> Prediction:
        hits = self.retriever.search(case.inputs["query"], limit=case.limit)
        return Prediction(retrieved_ids=[hit.id for hit in hits])

# Supply your own existing retriever. Omit "predictions" from this config.
report_path, summary = evaluate("eval.json", adapter=MyRetrieverAdapter(my_retriever))
```

The adapter receives only the case ID, input object and result limit; expected answers are passed only to scoring. This is a library boundary, not an OS sandbox. The adapter owner must enforce project/version filters, timeouts, retries and resource cleanup in the existing client. Live adapters may incur provider calls; saved-output mode makes none.

## Metrics and failures

Set retrieval reports precision, recall and F1 from TP/FP/FN. Ranked retrieval additionally reports Precision@k (TP/k, with empty slots penalized), reciprocal rank, graded nDCG@k and evidence-unit recall when supplied. Duplicate returned IDs receive no repeated true-positive credit and are counted as duplicate false positives. Missing expected outputs count as false negatives.

For ranked retrieval, empty-reference cases are reported separately and excluded from answerable-query ranking aggregates. A nearest-neighbor search may return passages for an unanswerable question; that alone does not prove an incorrect answer. Abstention needs a separate answer/status contract. Undefined ratios are null, never automatically 100%.

Missing prediction rows become MISSING. Adapter exceptions become ERROR, recording the exception type without arbitrary messages that might contain credentials. Failed cases stay in completion and effective recall denominators. Unknown case IDs, duplicate case IDs, malformed records and inconsistent label definitions fail before scoring. Any execution failure, unjudged returned ID or partial annotation makes the quality gate INCOMPLETE.

Optional gates are fractions, for example `"gates": {"precision_min": 0.9, "recall_min": 0.85}`. They apply to pooled ID-set precision/recall, not Precision@k or evidence-unit recall. Application-status mismatches fail the gate. With no thresholds, the gate is NOT_CONFIGURED. `mode: "release"` requires complete labels and `review.approved: true` on every reference. A recorded approval is an assertion, not proof of reviewer identity; independent review remains necessary. Draft data can produce diagnostic scores but never a release pass.

CLI exit codes: 0 for a completed diagnostic run or passed configured gates, 1 for failed/incomplete gates, and 2 for invalid input. CI users should supply thresholds and release mode; a development exit code of 0 is not quality certification.

The evaluator is `trace_impact.evals`; ingestion is `trace_impact.ingestion`. Both ship in the `trace-impact` package. No ingestion model calls, prompts or storage behavior are changed by the evaluator.
