# Evaluation design for the ingestion and impact agent

Status: proposed design for review. Prepared 2 October 2026. This document does not implement an evaluation runner or claim measured precision and recall. Configuration remains JSON with JSON Schema; no configuration UI is proposed.

Implementation update: [the independent retrieval scorer](simple-evaluation.md) supports saved JSONL outputs, field mappings and an optional Python adapter. Live graph witness-path checks and a bounded JSON-configured repeated graph-query runner now exist. Semantic judges, combined-answer evaluation and full-agent repeated runs remain proposed. See [current measured results and gaps](evaluation-results.md); synthetic fixture accuracy is not real product-impact accuracy.

The evaluation must answer two questions: does each stage produce correct evidence, and does the complete agent produce a correct, useful PR impact report? We will use versioned, human-reviewed golden datasets, deterministic scoring wherever possible, and explicit human review for semantic judgments. Each stage receives its own scorecard. A single average must not hide a broken stage.

The assignment asks for autonomous exploration, document ingestion, a Neo4j graph connecting requirements/UI/code, handling of absent UI coverage, and a readable blast-radius report. It also asks how correctness would be assessed across 100 runs of the same input. This proposal covers those requirements; it is an evaluation design supplement, not the entire assignment design document.

## 1 What the current results establish

The recorded Saleor run collected 9 documents, processed 121 chunks, indexed 121 vectors and published 96 requirement candidates. Of those candidates, 62 passed the current quote-grounding policy and 34 were rejected. The run explicitly records semantic verification as false and UI coverage as NOT_EVALUATED. See [the active run record](../artifacts/ingestion/active-run.json).

**62 / 96 is a grounding acceptance rate, not precision.** We do not yet know how many accepted statements are semantically correct or how many requirements were missed. Quote rejection also does not automatically establish an incorrect requirement: formatting differences can cause rejection.

The offline tests check software behavior and failure handling. They do not establish extraction quality. The manually curated [Saleor requirements](../examples/saleor/reference-requirements.json) are review material; several contain inferred UI acceptance criteria. They need source-by-source review before inclusion in a golden dataset.

Current extraction outputs and PR 1199 have already been inspected during development. Scores using these examples must be labeled development/regression results. New, uninspected source groups and PRs are needed for a defensible held-out generalization result.

## 2 The evaluation flow

1. Define the exact scope: source versions, code revision, browser starting state, backend fixtures and allowed actions.
2. Create reference answers independently from original evidence and review them.
3. Freeze the dataset, splits, matching rules, evaluator versions and acceptance thresholds.
4. Run the selected stage using only the permitted inputs. The target receives no golden answers.
5. Match predictions to reference items and record every correct, incorrect, missing and unjudged item.
6. Compute metrics, operational failures and evidence completeness.
7. Compare with a previous run on the same cases; inspect errors before changing the system.
8. Tune on development data. Use held-out cases only for the planned final evaluation.

Two paths remain separate:

```text
Frozen inputs -> Stage adapter -> Agent predictions --------+
                                                          +-> Evaluator -> Counts -> Scorecard
Reviewed reference answers -> Golden dataset repository ---+
```

The evaluation repository is not a source for production ingestion, embeddings or Neo4j knowledge. Evaluation results stay in separate artifacts. This avoids leaking answers into the system being measured.

## 3 What goes into golden data

A golden item is an approved reference answer with evidence and a version, not an LLM response designated as correct. Models may draft labels; a human must check them against original evidence. For important or disputed cases, use two independent reviewers and adjudication. If only one reviewer is available, label the dataset single-reviewed and retain the limitation.

For the first ingestion benchmark, review all 9 saved source snapshots, not just the 96 candidate outputs. Reviewing candidates alone can estimate precision but cannot find missing requirements and therefore cannot measure recall. Review negative passages and the 65 chunks currently marked as containing no requirements as well.

Annotate against source sections and evidence spans independently of the current chunk boundaries. Each reference requirement should be atomic: one actor, behavior, precondition set and expected outcome. Record exceptions, layer, support type and applicability. Keep backend facts separate from claims that a frontend implements them.

Use stable reference IDs and original snapshot hashes. Store evidence offsets in a versioned normalized text representation, with links to raw evidence. At evaluation time, map source spans to whatever chunks the tested chunker produced. Never define ground truth solely by IDs generated by the system under test.

| Golden dataset | Reference contents | How correctness is established |
| --- | --- | --- |
| Sources and parsing | Expected documents, content blocks, headings, order, tables, links and noise exclusions | Human review of frozen source files |
| Requirements | Atomic statements, exact evidence, conditions, layer, scope, valid paraphrases and negative sections | Independent source annotation |
| Grounding and semantic validation | Supported and unsupported claim/evidence pairs, including fabricated quotes and reversed conditions | Reviewed entailment and quote decisions |
| Retrieval | Questions, relevant evidence units, relevance grades and unanswerable questions | Review against a bounded frozen corpus |
| Browser exploration | Reachable states, actionable controls, transitions and task outcomes within the declared budget | Manual exploration and reproducible traces |
| Code analysis | Symbols and typed edges at an exact commit, supported language constructs and unresolved cases | Reviewed small repositories and bounded real-code slices |
| Cross-layer mappings | Requirement-to-UI and UI-to-code links with supporting evidence and hard negative links | Joint review of source, UI trace and code |
| Coverage and absence | Evidence-based coverage labels and reasons for each in-scope requirement | Review under fixed crawl scope and fixtures |
| PR impact | Changed symbols, impacted elements/flows/requirements, unaffected controls, critical effects and uncertainty | Independent diff review plus controlled behavior checks |
| Final report | Required claims, valid citations, prohibited claims and readability rubric | Product-oriented human review |

Include difficult cases: negation, conditional eligibility, contradictory versions, multiple sources for one fact, duplicated content, irrelevant setup instructions, API-only features, unavailable fixtures, dynamic imports and changes with no UI impact. Synthetic cases are useful for precise edge conditions, but report their scores separately from real Saleor cases.

### Annotation lifecycle

Use DRAFT -> REVIEWED -> FROZEN. Disputed items remain DISPUTED and cannot enter a release gate. Store author, reviewers, decision reason and timestamps. Corrections create a new dataset version and require rescoring both comparison runs.

Label annotation completeness per case: EXHAUSTIVE_WITHIN_SCOPE or PARTIAL. For a partially labeled graph or retrieval corpus, an unmatched prediction is UNJUDGED until reviewed, rather than automatically false. Official precision/recall gates require complete adjudication for their bounded evaluation universe.

### Dataset splits

Maintain development, calibration and held-out groups. Development is for prompts and debugging; calibration is for matching thresholds, judges and confidence policies; held-out cases are for final scoring. Split by source/topic/flow/repository/PR family, not randomly by overlapping chunks. All paraphrases and variants of the same case stay in one split.

A small number of source groups cannot support strong broad claims. Begin with a complete development benchmark on current Saleor evidence; add genuinely fresh held-out groups later. Gold retrieval labels remain hidden even though the retrieval corpus itself must be available to the system. Never place reference answers or impact expectations in that corpus.

## 4 Precision and recall in simple terms

For a set of predictions and a fully labeled reference set:

- True positive (TP): a correct prediction matched to a golden item.
- False positive (FP): a prediction that is incorrect or outside the declared scope.
- False negative (FN): a golden item the system failed to produce.

Precision = TP / (TP + FP). It answers: of what we produced, how much was correct?

Recall = TP / (TP + FN). It answers: of what we should have found, how much did we find?

F1 = 2TP / (2TP + FP + FN). It balances precision and recall.

Example only: reviewers identify 20 requirements. The agent produces 18; 15 match the reference and 3 are wrong. There are 5 missed requirements. Precision is 83.33%, recall is 75.00%, and F1 is 78.95%. These are illustrative numbers, not Saleor results.

Store counts and fractions; display percentages. Report micro scores from pooled counts, macro scores across eligible cases, and slices by loader, topic, authority and difficulty. Show sample sizes beside percentages so a tiny slice cannot look conclusive.

If there are no predictions, precision is undefined (null), not automatically 100%. If a positive case has no predictions, recall and F1 are zero. If both reference and prediction sets are empty, precision/recall/F1 are undefined; the case contributes to a separate negative-case pass rate. An empty reference with false predictions has precision and F1 zero, with recall undefined. Undefined values are excluded from macro averages with their count reported.

## 5 Matching predictions to reference answers

Exact IDs work for known entities and edges. Requirement text needs semantic comparison: valid paraphrases must match, while a shared keyword must not be enough.

Proposed matching sequence:

1. Validate structure, source version and evidence references.
2. Canonicalize only nonsemantic differences using a versioned policy. Preserve negation, amounts, conditions and modality.
3. Retrieve possible matches using IDs, source spans or semantic similarity. Similarity only proposes matches.
4. Approve equivalence using actor, action, object, conditions, expected outcome, layer and scope. Use reviewed rules or human-adjudicated judgments.
5. Perform deterministic one-to-one matching between eligible predictions and golden items, with stable tie breaking. Count unmatched items.

Require atomic predictions. A compound prediction must be split into auditable claims before scoring; every additional unsupported claim counts against precision. A vague prediction cannot claim credit for multiple specific requirements. Equivalent duplicate outputs beyond the one matched item count as duplicate false positives, with a separate duplicate rate. Equivalent references across documents are merged into one requirement with multiple evidence records.

Strict matching gives full credit or no credit. Report partial field agreement diagnostically, without silently awarding fractional true positives. Wrong preconditions or polarity are a mismatch even if most words are identical.

Keep semantic correctness, citation correctness and scope correctness as separate dimensions. Report raw extraction quality, accepted-candidate quality, and false rejections by the grounding filter. A better-looking accepted precision can otherwise hide loss of correct requirements.

### Optional LLM judge

Use a judge behind an interface for semantic match proposals or report rubrics. It receives the reference evidence, prediction and fixed rubric; source text is data, not instructions. It returns a structured verdict, evidence and reason. Record provider, model, prompt hash and rubric version.

Calibrate against human-labeled positive and negative pairs. Measure the judge's own precision/recall and disagreement rate. Audit false acceptances, false rejections and low-confidence cases. A different model can reduce some shared errors but does not establish independence or correctness by itself. Uncalibrated judge scores are advisory and cannot be release gates. Judge timeouts produce JUDGE_ERROR, never a passing score.

LangSmith documents code, LLM and summary evaluators; these are useful optional adapters, while our reference data and scoring contracts remain local and provider-independent. [LangSmith evaluation types](https://docs.langchain.com/langsmith/evaluation-types).

## 6 Metrics for every stage

| Stage | Primary quality metrics | Additional checks |
| --- | --- | --- |
| Load and parse | Expected content-block precision/recall; required document success rate | Correct hashes/version, heading/order fidelity, table preservation, noise leakage, metadata correctness |
| Chunk | Requirement evidence-unit containment recall; intact context rate | Oversized blocks, duplication ratio, orphan references, split conditions/exceptions |
| Extract requirements | Atomic requirement precision, recall and F1 | Layer/scope correctness, duplicate rate, missed negative conditions, false claims on negative sections |
| Validate candidates | Precision/recall of accepting genuinely supported claims | Citation correctness, semantic entailment, false rejection rate; separate exact-quote and semantic decisions |
| Embed and persist | Record completeness, reference integrity and reproducible writes | Dimensions, finite vectors, model/profile isolation, run isolation, idempotency, resume correctness |
| Retrieve | Evidence Recall@k, chunk Precision@k, MRR and nDCG@k | Unanswerable-query handling, duplicate evidence, wrong-version retrieval, latency |
| Explore UI | State/transition/element precision and recall against bounded reachable gold | Task success, artifact completeness, duplicate states, budget use and blocked sessions |
| Analyze code | Entity and typed-edge precision/recall, by supported construct | Correct commit, path/symbol resolution, alias/re-export handling, unresolved dynamic relationships |
| Map layers | Link precision/recall/F1 separately for each relationship type | Evidence support, orphan links, per-link confidence and abstention |
| Assess coverage | Per-class precision/recall and macro F1 | False absence claims, incorrect functionality claims, unknown/blocked rate |
| Analyze PR impact | Precision/recall for elements, flows and requirements separately | Critical miss count, no-impact controls, graph path validity, supported vs uncertain impact |
| Generate report | Supported-claim precision and required-claim recall | Citation validity, evidence entailment, uncertainty, contradictions and readability |

Embedding quality is primarily judged through retrieval effectiveness, not through a made-up embedding accuracy percentage. Persistence is checked through deterministic invariants, not semantic scoring. A valid graph edge is checked both for storage integrity and, separately, whether its claimed relationship is true.

### Retrieval details

Use k = 3, 5 and 10. Keep two related measures explicit:

- Chunk Precision@k: relevant returned chunks / k, with unfilled result slots counted as nonrelevant. Record how many chunks were returned. Multiple chunks for one fact can all be relevant; report their redundancy separately.
- Evidence Recall@k: distinct required evidence units covered by the top-k chunks / total required evidence units for the query. Repeated chunks for the same fact receive no extra recall credit.
- MRR: mean reciprocal rank of the first relevant result across answerable queries; no hit contributes zero.
- nDCG@k: rank quality using frozen graded relevance labels and log-discounted gain; no-relevance cases are excluded and scored as unanswerable cases.

Some questions need several facts; define each required evidence unit and acceptable alternative supporting spans. Pin the chunking version for chunk-rank comparisons. For comparisons across chunkers, prefer stable evidence-unit recall and disclose the changed candidate inventory.

For unanswerable questions, measure correct abstention and false-answer rate. A low similarity score alone is not proof of unanswerability. Validate any refusal threshold on calibration data.

Ragas supplies reference and model-based RAG metrics, including context precision, context recall and faithfulness. They can be optional diagnostics; record exact metric names and versions because their definitions need not equal the set/count metrics above. [Ragas metric catalog](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/).

### UI coverage and absence

Score against the states reachable under the declared account, channel, fixtures, start URLs and action budget. An agent that exhausts its budget has incomplete exploration, not proof of absent functionality. Manual paths are available to the scorer, not to the autonomous planner being evaluated.

Use separate coverage and observed-behavior fields. The coverage states are NOT_EVALUATED, OBSERVED, NOT_OBSERVED and BLOCKED. A visible control is structural evidence; behavioral coverage requires an executed action with outcome evidence. Behavior can independently be PASS, FAIL or NOT_EXECUTED.

For example, a voucher form can be observed and its total update can fail. Calling the whole feature absent would be incorrect. NOT_OBSERVED is scoped to the captured crawl; the report must not turn it into a global claim that the application lacks the feature.

Include negative coverage cases: missing fixtures, authentication requirements, feature flags, unreachable pages, API-only requirements and incomplete crawls. Count unsupported absence claims explicitly as a critical error category.

### PR impact and the Saleor example

Build knowledge from baseline documents, code and autonomous UI observations. Freeze predictions after supplying the actual diff. Only then use the patched deployment and reviewer expectations to validate outcomes. Record diff-only and diff-plus-description experiments separately.

The existing PR 1199 voucher scenario is suitable for regression demonstration. Its gold should include affected voucher/checkout behavior and reviewed unaffected controls. Preserve the exact baseline/patched commits, backend fixtures and separate fresh checkout sessions. Do not infer actual impact solely from changed filenames or from the agent's own dependency graph.

Maintain separate gold labels for plausible risk and observed behavioral change. An affected flow may improve or remain functional; affected does not mean broken. Claims that requirements lose coverage require specific evidence or an explicit risk label. Dynamic checks establish observed outcomes, while reviewed dependency reasoning can justify risks that a single test did not exercise.

One real PR supports a case study, not broad PR-impact accuracy. Add independent PRs and no-impact changes before making general claims.

## 7 Three evaluation modes

**Isolated stage evaluation:** give the stage correct reviewed upstream inputs, then score its output. For example, test mapping with reviewed requirements, UI and code entities. This reveals whether the mapper itself is correct.

**End-to-end evaluation:** use actual upstream predictions throughout the pipeline. This measures the user-facing outcome, including errors inherited from earlier stages.

**Repeated-run evaluation:** run the same frozen cases several times to measure variability and reliability. Keep this separate from the first two modes in reports.

Report isolated and end-to-end results together. A mapping failure caused by missing code entities is an end-to-end error but should not be misdiagnosed as an isolated mapping failure.

## 8 Handling 100 runs of the same input

First use 3 to 5 fresh runs to debug the harness. Then, for the final stability experiment, execute 100 scheduled runs of one declared bounded task or case suite. A whole-system suite and one small extraction case are different experiments and must be named accordingly. Do not silently describe a small case as 100 complete application runs.

Freeze source snapshots, revisions, backend fixtures, model ID, prompts, evaluator versions and budgets. Record provider response/version information when available; hosted model aliases may still change. Use fresh sessions for browser runs and independent output directories. Seeds, if supported, are recorded rather than treated as a guarantee.

Disable extraction/generation response-cache reuse between independent model samples. Immutable source artifacts and fixed embeddings may be reused when they are not the stage being measured. Run a separate cache-replay test for determinism and recovery. Cached outputs cannot measure model variability.

For each attempt record:

- Completion status, retries, model errors, latency and token/call usage.
- TP/FP/FN and stage gates, plus critical omissions and unsupported claims.
- Dataset, input, code, prompt, model, configuration and evaluator fingerprints.
- Whether caches were used and which stages were actually rerun.

The summary includes successful completed runs / 100, correct completed runs / 100, precision/recall/F1 distributions across completed runs, worst observed cases, critical-failure frequency and optional prediction-set agreement. Agreement measures consistency, not correctness.

Use a binomial confidence interval for the success rate and paired case comparisons when comparing two versions. Bootstrap across independent source/flow groups for dataset quality intervals; do not treat overlapping chunks or 100 repetitions of one case as 100 independent examples of product coverage. A perfect observed rate still has uncertainty.

Provider failures stay in the scheduled-attempt denominator. Conditional quality scores on completed outputs are reported separately from effective end-to-end coverage across all attempts. An aborted attempt contributes missing expected items to effective recall; it is not silently converted into a successful empty result. Within an attempt, apply the configured bounded retry policy and report the final result. Persist checkpoints after every case and enforce the call budget.

## 9 Simple reusable library design

Keep evaluation outside production orchestration. Reuse the existing registry pattern; add only boundaries that support a real replacement.

| Component | Responsibility | Extension point |
| --- | --- | --- |
| GoldenDatasetRepository | Load/version/check reference cases and splits | JSONL first; another repository later |
| StageAdapter | Run one pipeline stage or load its saved predictions | One registered adapter per stage |
| Evaluator | Compare a prediction with a reference under a fixed rubric | Deterministic, reviewed-semantic or optional judge implementation |
| EvaluationRunner | Validate config, isolate inputs, execute, checkpoint and resume | Application service, no provider SDK logic |
| MetricCalculator | Aggregate counts, rates, slices and uncertainty | Pure functions; no model calls |
| ReportWriter | Save per-case results and summary | JSON and Markdown artifacts initially |

Illustrative contracts, not implemented APIs:

```python
class GoldenDatasetRepository(Protocol):
    def load(self, dataset_id: str, version: str, split: str) -> Dataset: ...

class StageAdapter(Protocol):
    def run(self, inputs: CaseInputs, context: RunContext) -> Prediction: ...

class Evaluator(Protocol):
    def evaluate(self, prediction: Prediction, reference: Reference,
                 context: EvaluationContext) -> CaseResult: ...
```

The runner passes only CaseInputs to the StageAdapter. Reference answers are a separate object passed only to Evaluator. Use frozen run manifests and restrict file paths/namespaces so this is more than a naming convention. For a held-out run, the target should execute with an input/artifact allowlist that excludes golden labels.

Pydantic models define configuration and result contracts. The registry provides allowed names, descriptions and implementation-specific options. The existing JSON Schema mechanism supplies editor completion. LangChain is used only inside optional model adapters; deterministic metrics need no LLM framework. LangSmith/Ragas adapters are optional later integrations, not mandatory accounts or databases.

### Data contracts

DatasetManifest records dataset ID/version/hash, scope, split groups, annotation completeness, reviewer status, source versions and case file references.

GoldenCase contains case ID, stage, group ID, tags, permitted inputs, reference output, evidence references, criticality and adjudication. Runtime chunk IDs can be attached for a particular experiment but never replace stable gold IDs.

Prediction contains case ID, output items, source/artifact references, producing configuration, completion status, timing and provider usage. A cached artifact can be scored without repeating model calls.

CaseResult contains status, matched pairs, TP/FP/FN, unjudged items, per-field errors, evidence decisions, metric numerators/denominators and gate reasons. An auditor must be able to reconstruct a percentage from those records.

EvaluationRun contains fingerprints, selected cases, repeats, completed/failed/blocked counts, aggregate metrics, comparison run ID and final gate status.

### Proposed JSON configuration

This is a proposed evaluation configuration, separate from the implemented ingestion project JSON. These evaluation fields and commands do not exist yet.

```json
{
  "$schema": "../schemas/evals/evaluation.schema.json",
  "schema_version": 1,
  "project_config": "../configs/ingestion/saleor/project.json",
  "dataset": {
    "id": "saleor-ingestion",
    "version": "1.0.0",
    "split": "development",
    "manifest": "../evaluation/golden/saleor/v1/manifest.json"
  },
  "execution": {
    "mode": "score_saved_outputs",
    "repeat_count": 1,
    "failure_policy": "record_and_continue",
    "generation_cache": "existing_artifacts_only",
    "max_model_calls": 0
  },
  "stages": [
    {
      "name": "requirement_extraction",
      "adapter": "saved_ingestion_run",
      "options": {
        "run_directory": "../runs/saleor-storefront/44dd49dc16a84d2e82153ebd7d65c6ee"
      },
      "evaluator": "reviewed_requirement_match",
      "metrics": ["precision", "recall", "f1", "citation_correctness", "duplicate_rate"],
      "gates": {
        "precision_min": 0.90,
        "recall_min": 0.85,
        "critical_misses_max": 0,
        "unjudged_predictions_max": 0
      }
    }
  ],
  "report": {
    "formats": ["json", "markdown"],
    "slices": ["source_id", "authority", "topic"],
    "output_directory": "../artifacts/evaluation"
  }
}
```

Paths resolve relative to the evaluation config; immutable manifests record their hashes. The proposed schema supplies enum suggestions, descriptions, required fields and numeric bounds. Unknown registered-stage/evaluator names fail early. Stage-specific metrics must be compatible with the selected evaluator. `repeat_count > 1` with saved-output mode is rejected for a model-variability experiment.

Add future stages by implementing and registering a StageAdapter/Evaluator pair with typed options. Add their golden cases separately. No switch statement hardcoded to Saleor should be needed in the runner.

### Artifact layout

```text
evaluation/golden/saleor/v1/manifest.json
evaluation/golden/saleor/v1/requirements.jsonl
evaluation/golden/saleor/v1/negative_sections.jsonl
evaluation/golden/saleor/v1/review_decisions.jsonl
configs/evaluation.saleor.json
schemas/evals/evaluation.schema.json
artifacts/evaluation/<run-id>/manifest.json
artifacts/evaluation/<run-id>/predictions.jsonl
artifacts/evaluation/<run-id>/case-results.jsonl
artifacts/evaluation/<run-id>/summary.json
artifacts/evaluation/<run-id>/report.md
```

These paths are proposed. Store labels in version control when appropriate; keep large evidence, secrets and ephemeral backend/session data outside public commits. Hash-link evidence artifacts. Production indexing must explicitly exclude the evaluation directory.

## 10 Gates and failure policy

Thresholds below are proposed initial engineering targets, not measured results, assignment requirements or industry standards. Calibrate on development/calibration data and freeze them before held-out scoring. Report changes to thresholds, not just changes in scores.

| Area | Proposed initial gate |
| --- | --- |
| Dataset readiness | Every scored case frozen; zero unresolved required annotations; bounded universe declared |
| Data integrity | 100% required references/hashes valid; no wrong-run or wrong-version records |
| Requirement extraction | Precision >= 90%; recall >= 85%; zero critical requirement misses |
| Citations | 100% references resolve; >= 95% claims supported by their cited evidence |
| Retrieval | Evidence Recall@5 >= 90% on eligible answerable cases; zero cross-project/version leaks |
| Typed code relationships | 100% on deterministic supported-construct fixtures; real-code slice scores reported separately |
| Cross-layer mapping | Precision >= 90%; recall >= 85%; unknown-link coverage reported |
| PR impact | Requirement/flow recall >= 90%; precision >= 80%; zero critical misses |
| Coverage and reporting | Zero unsupported absence or confirmed-breakage claims; all critical report claims evidenced |

No blanket browser-coverage threshold is selected before its reachable-state inventory and action budget are agreed. Task success and discovery recall should have per-suite targets. A stage with too few examples gets INSUFFICIENT_DATA rather than a production-quality claim, even if it meets the numerical threshold. Minimum sample requirements are dataset-specific and must be frozen in the manifest.

Case execution statuses include COMPLETE, TARGET_ERROR, JUDGE_ERROR, BLOCKED_BY_FIXTURE and INVALID_REFERENCE. Gate outcomes are PASS, FAIL, INCOMPLETE or INSUFFICIENT_DATA. A skipped or unimplemented required stage cannot pass. Do not create a zero-score row that looks like a measured result for code that does not exist.

Distinguish predeclared out-of-scope items from runtime failures. Exclusions have documented reasons and counts. Runtime outages, missing predictions and blocked fixtures remain visible in scheduled-case completion metrics. Full-run PASS requires every required stage to have adequate judged data and pass its gates.

## 11 Reports and comparison

The human-readable report should begin with what passed, what failed, what was not evaluated and the scope of the conclusion. Include counts behind every percentage, dataset/revision identity and links to examples.

For each stage show TP/FP/FN, precision/recall/F1 when defined, completion and annotation coverage, critical failures, latency and call usage. Show false positives and missed golden items side by side with evidence and the evaluator's reason. Group failures into source loss, parsing damage, missing conditions, unsupported inference, wrong layer, incorrect citation, wrong mapping, missing exploration, and infrastructure failure.

Compare candidate and baseline versions on exactly the same frozen cases. Report per-case regressions and improvements, not only average deltas. If inputs or gold change, re-score both. Token counts are measured; cost is only an estimate when a dated provider price table is available.

For final reports, a human rubric scores factual correctness, traceable citations, clear affected flows, appropriate uncertainty and readability for a QA lead. Keep readability scores separate from factual correctness; fluent explanations can still be wrong.

## 12 Implementation order after review

1. Agree on atomic requirement labels, strict matching rules, negative cases and provisional gates.
2. Create the golden ingestion dataset by reviewing all frozen source snapshots. Draft from evidence first; inspect model candidates afterwards for errors and omissions.
3. Implement JSON contracts, a saved-output stage adapter, deterministic metrics and JSON/Markdown reporting. Score the existing run without new extraction calls.
4. Review false positives, false negatives and false rejections. Fix ingestion using development data, then compare reruns against the same gold.
5. Add retrieval questions and evidence judgments before choosing retrieval/reranking settings.
6. As UI, code, mapping and PR stages are implemented, add their stage-specific gold and isolated/end-to-end evaluations.
7. Run the repeated-input experiment and independent held-out evaluation once the bounded complete path works.

The immediate next work is golden ingestion data and an offline scorer. Semantic judges, large repeat campaigns and later-stage benchmarks follow only after their references and target implementations are ready.

## 13 Review decisions

The recommended starting choices are local JSON/JSONL reference data, human-reviewed atomic requirements, deterministic one-to-one scoring, separate raw/accepted extraction metrics, and saved-run evaluation before spending more model quota. Keep optional model judges advisory until calibrated. Preserve independent evidence for the assignment's absence and uncertainty requirements.

Review the definition of a correct requirement, the coverage labels, and the provisional gates first. Those decisions affect the credibility of every later percentage more than the choice of evaluation framework.
