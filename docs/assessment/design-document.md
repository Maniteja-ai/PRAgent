# Testsigma PR Impact Agent — Design Document

**Assessment project:** Testsigma AI Engineer assignment
**Implementation:** webhook-driven PR impact analysis with separate knowledge ingestion
**Status:** working prototype; one live PR run completed with an explicit browser-coverage gap
**Date:** 5 October 2026

## 1. Executive summary

This project builds a knowledge-backed assistant that helps QA and engineering teams understand the likely customer impact of a pull request. It gathers product documentation, source-code structure and selected browser observations into Qdrant and Neo4j. When a PR arrives, a webhook queues work; the worker loads the PR and diff, retrieves relevant evidence, asks a configured model to explain likely impacts, applies safety checks and saves a readable report.

The central design decision is to keep **searchable text** and **explicit relationships** in stores suited to each job. Qdrant holds embedded code/document chunks for semantic search. Neo4j holds source files, symbols, dependencies, requirements/citations, observed routes and evidence-backed mappings. A browser observation or graph edge is evidence of a relationship; a semantic similarity score alone is not.

A live run against `Maniteja-ai/storefront#1` completed as `COMPLETED_WITH_GAPS`. It exercised GitHub, Gemini, Neo4j, Qdrant and configured Playwright checks. The browser checks confirmed the checkout address and control presence only. They did not apply/remove a voucher or validate updated totals. This is a prototype, not a claim that the full assignment crawler or all acceptance criteria are finished.

## 2. Problem and users

A reviewer sees changed files and often cannot tell which product areas, screens, user journeys or tests may be affected. QA then has to rediscover dependencies and decide what to retest. The agent should turn a PR into a traceable impact summary:

- what code changed;
- which requirements, components, routes and user journeys have evidence-backed relationships to that code;
- what likely user-visible behavior could change;
- which checks actually ran and passed;
- what remains unknown and should be reviewed by a person.

The main reader is a QA engineer or product-minded reviewer, so the report should use clear language, point to evidence and distinguish an impact prediction from an executed test result.

## 3. Scope and explicit decisions

### In scope in the current prototype

- A GitHub webhook receiver validates requests and places work on a durable SQLite queue.
- A worker fetches PR metadata/files/diff through a GitHub adapter.
- Retrieval combines Qdrant semantic results and schema-aware Neo4j code-graph results.
- Gemini plans bounded graph retrieval and creates impact findings from evidence.
- Guardrails validate request/evidence/decision/output and preserve gaps.
- Optional Playwright browser exploration and behavior checks run using configured adapters/scenarios.
- Markdown reports and run/stage history are persisted locally.
- Offline tests cover the components; one live PR run is retained with a draft quality evaluation.

### Not claimed as complete

- The browser does not autonomously crawl the full application, choose arbitrary actions or generate a complete screen-transition graph. Ingestion uses configured seed paths and a scripted checkout journey.
- Current browser output is not a complete DOM/screenshot/control graph. Route-to-code links are stronger than exact control-to-code links; requirement-to-UI/code coverage is incomplete.
- The voucher scenario was a presence/URL smoke test, not functional voucher testing.
- A single LLM-judge evaluation case is not a benchmark and its labels are awaiting human review.
- Production deployment, webhook operations, security review, concurrency/load testing and organization-wide privacy controls are not demonstrated by this local prototype.

These boundaries are represented as coverage gaps in reports instead of being silently inferred as success.

## 4. High-level architecture

```mermaid
flowchart LR
  subgraph Ingestion[Offline or scheduled knowledge ingestion]
    Inputs[Docs URLs + repository checkout + configured browser journey]
    Extract[Load, parse, syntax-aware chunk, extract candidates]
    Embed[Embedding model]
    Inputs --> Extract --> Embed
    Embed --> Q[(Qdrant: text chunks + metadata + vectors)]
    Extract --> N[(Neo4j: files, symbols, dependencies, routes, evidence)]
  end
  subgraph Agent[PR analysis service]
    Hook[GitHub webhook] --> Queue[Durable SQLite queue]
    Queue --> PR[GitHub PR adapter: metadata + changed files + diff]
    PR --> Retrieve[Qdrant search + schema-aware Neo4j retrieval]
    Q --> Retrieve
    N --> Retrieve
    Retrieve --> Model[Gemini: evidence-grounded impact analysis]
    Model --> Safety[Guardrails + behavior checks + gap recording]
    Safety --> Report[Markdown QA report + run history]
  end
```

Ingestion and analysis have independent configurations and dependencies. The ingestion command can refresh the code/UI graph without embedding or LLM calls using `--code-ui-only`. The analysis process reads the shared local Qdrant store and configured Neo4j database.

### Why two stores?

Qdrant answers “which text chunks are semantically related to this PR/query?” Neo4j answers “what known, typed relationships connect these symbols, files and routes?” A vector match is a candidate source of context; it does not establish a dependency edge. Graph relationships include `IMPORTS`, `DECLARES`, `CALLS`, `RENDERS`, `EXTENDS`, `IMPLEMENTS`, route declarations and evidence-backed `AFFECTS_UI` mapping.

## 5. Main PR flow

```mermaid
sequenceDiagram
  participant GH as GitHub
  participant WH as Webhook/API
  participant W as Worker + LangGraph
  participant DB as GitHub adapter
  participant K as Qdrant + Neo4j
  participant L as Gemini
  participant B as Playwright
  participant R as Report/history
  GH->>WH: pull_request event + signature
  WH->>WH: validate event/signature, enqueue
  W->>DB: fetch PR, changed files and diff
  W->>K: retrieve code, docs, graph routes and evidence
  W->>B: run configured UI/behavior checks (if applicable)
  W->>L: request evidence-grounded impact findings
  W->>W: validate evidence, safety, test scope and coverage gaps
  W->>R: save Markdown report + stage/run history
```

The current graph stages are `VALIDATE_PR_REQUEST → FETCH_PULL_REQUEST → RETRIEVE_KNOWLEDGE → EXPLORE_UI → ANALYZE_IMPACT → VERIFY_BEHAVIOR → CREATE_REPORT`. Each stage is wrapped by an evaluation recorder decorator, so stage input/output summaries can be captured without inserting evaluation-specific code into every stage. The decorator records; it does not itself prove quality.

### Runtime details

1. **Receive:** FastAPI validates the GitHub webhook signature/event and persists a job in SQLite. The worker can resume queued jobs after service restart.
2. **Load PR:** The GitHub adapter obtains metadata, changed paths and bounded diff text. Diff evidence is hashed and truncated within configuration budgets.
3. **Retrieve:** Qdrant returns text/code chunks; Neo4j retrieval reads the graph schema, requests bounded parameterized Cypher from the configured model, validates required PR/revision parameters and executes in a read-mode, time-bounded transaction. Database credentials should independently be read-only.
4. **Explore:** Optional browser capture and configured assertions contribute browser evidence. An unavailable adapter or a smoke-only scenario creates a gap rather than a false functional PASS.
5. **Analyze:** The decision model receives the PR and validated evidence. Findings must cite evidence IDs. Direct PR diff evidence is included when it is available.
6. **Guard:** Guardrails validate requests, retrieved evidence, model findings and rendered output. Unsupported claims or unsafe evidence are rejected/marked, and gaps are carried forward.
7. **Report:** The report status is `COMPLETED` only when required stages have no known gaps; otherwise it is `COMPLETED_WITH_GAPS`. The run and report are stored in SQLite.

## 6. Knowledge ingestion and data model

### Configured inputs

The Saleor project config is split into a root manifest and six readable files: `input.json`, `models.json`, `chunking.json`, `storage.json`, `constraints.json`, and `evaluation.json`. Input config pins the storefront baseline and code roots; selects nine public docs/API sources; configures an HTTPS baseline URL, seed paths and a scripted product-to-checkout journey. Model config selects Gemini embedding (`gemini-embedding-2`), requirement extraction (`gemini-3.5-flash-lite`) and evaluation judge (`gemini-2.5-flash`). Chunking uses section-aware document chunking and syntax-aware code chunking with a maximum chunk size. Storage selects local artifacts, Neo4j and Qdrant with cosine similarity. Constraints bound retries, timeouts, documents/files/pages and source failure policy. Evaluation config controls recorder output and dataset/metrics.

Credentials are environment variables, not JSON. Exact repository revisions and artifact hashes support reproducibility. Current online documentation URLs are labeled unpinned; their version compatibility with the historical baseline remains a limitation.

### Qdrant

Stores embedded text chunks for documentation and code. Payload metadata includes source/repository identity, revision, file path, symbol or section information, line range and related tags. Code extraction is syntax-aware and produces function/method/component/class-context chunks where the parser can identify symbols. Embeddings make content discoverable even when the query uses different wording.

### Neo4j

Stores typed entities and relationships: source files, code symbols, requirements/candidates, source documents/chunks/citations, browser observations, routes and code/UI mappings. Code dependencies are parsed statically where possible. A route link is considered confirmed only when a supported framework route, a configured browser observation or explicit code tag provides evidence. The Saleor refresh had 268 code files, 596 import relationships, four browser observations and four evidence-backed route mappings. This is not proof that every visible control or requirement is mapped.

Requirement extraction currently produces cited candidates, and artifacts record evidence. Semantic entailment and frontend scope still need human review; the earlier document run reported 62 grounded candidates but all 96 assessments remained `NOT_EVALUATED`. Do not interpret “quote found” as “requirement confirmed.”

## 7. Confidence, ambiguity and human review

Evidence should carry a source, revision/content hash, location and relationship basis. Report claims use evidence IDs; the PR diff is primary for what changed. Documentation is useful for API expectations but may not match a historical app baseline when unpinned. Static imports indicate reachability, not necessarily runtime visibility. Browser presence checks prove only presence at the captured state.

A practical confidence policy is:

- **High:** directly changed code or a deterministic graph edge with matching revision and source location; phrase as “the change does…”
- **Medium:** multiple compatible graph/vector/doc signals but no direct runtime assertion; phrase as “may affect” and name the basis.
- **Low/unknown:** candidate mapping, missing route, stale revision, conflicting sources or unavailable browser state; report a coverage gap and request human review.

The current implementation validates evidence and rejects/quarantines unsafe or unsupported cases, but it does not yet provide a fully calibrated statistical confidence model. The sample case’s dataset is marked `DRAFT_PENDING_HUMAN_REVIEW`; scores should not gate releases. A human should review graph mappings, expected-impact labels, findings with low/ambiguous evidence and any gap affecting a critical flow.

## 8. Safety and privacy controls

- Webhook signature validation, event allowlisting, request-size limits and a durable queue protect the entry boundary.
- Graph retrieval is parameterized, row/time bounded and intended for a least-privilege read-only Neo4j identity.
- Browser navigation is constrained by HTTPS host allowlists and configured action/time limits.
- Model evidence is validated; prompt-injection handling and sensitive-data actions are configurable in guardrail settings.
- Output is checked before persistence. Run history and stage recording are local SQLite/JSON artifacts in the configured environment.
- Secrets remain in environment variables and ignored `.env` files; never paste PEM/private keys into reports, prompts or commits.

Before production, add organization-grade DLP/PII coverage for all persisted artifacts and logs, retention/deletion controls, encrypted managed storage, key rotation, webhook replay protection, worker authentication and operational alerting. Current controls are prototype safeguards, not a compliance certification.

## 9. Evaluation and repeatability

### What exists

- Offline unit/component tests: **87 agent tests** and **28 ingestion tests** passed on 5 October 2026.
- A saved live PR run: `live-e2e-pr1-scope-gap-20261005`.
- A PR-impact quality dataset with one draft case and a Gemini judge evaluating expected-impact coverage, atomic-claim support, relevance, evidence relevance and test-scope honesty.
- An ingestion retrieval dataset `saleor-retrieval-v0.1` with manifests, input/label/review/schema directories. It is a draft; mapping precision/recall and requirement grounding are not enabled in the current config.

The live PR evaluation reported precision 100%, recall 100%, F1 100%, faithfulness 100%, claim relevance 100%, and evidence relevance 56.2%. It matched six report claims to three expected impacts in one case. Those 100% scores are not statistically meaningful: one draft case and one LLM judge request cannot establish general quality. Evidence relevance at 56.2% is an explicit retrieval-quality issue.

### Reproduce

From `agents/`:

```powershell
uv run python evaluation/agent_quality/run_quality_eval.py --run-id live-e2e-pr1-scope-gap-20261005
```

This re-evaluates a saved run and makes one configured judge call; it does not repeat PR ingestion, graph retrieval, browser checks or code indexing. The dataset needs independent human review before score interpretation. For stable evaluation, expand it with independently reviewed PRs, measure multiple cases, compare against human labels and report per-category confidence/variance. Add deterministic checks for citations, hashes, stage completion and browser assertion wording. Keep development/test data out of production code paths.

## 10. Trade-offs

- **Webhook plus worker instead of a local-only command:** supports realistic PR arrival and survives transient process restarts; adds queue and deployment operations.
- **Qdrant plus Neo4j:** combines fuzzy semantic search with explainable dependency traversal; requires synchronized revisions and explicit provenance.
- **LLM-generated Cypher:** adapts to graph schema and PR context; it is bounded and validated but adds latency/cost and needs adversarial query testing. Deterministic graph queries remain preferable for well-known retrieval patterns.
- **Scripted browser actions first:** repeatable and bounded; less broad than autonomous exploration. This is a conscious prototype boundary, not completion of the assignment’s autonomous crawler requirement.
- **Persisted run artifacts:** support audit and demos; need retention, privacy and size policies before organizational deployment.

## 11. Three priorities for next week

1. **Complete evidence-backed UI coverage:** build a bounded autonomous crawler that captures screenshots, DOM/control identifiers and transitions; connect observed controls and routes to code through explicit evidence; evaluate mapping precision/recall with independently labeled pages.
2. **Make end-to-end behavior checks meaningful:** seed a deterministic checkout, apply a valid and invalid voucher, remove it, and assert error state and totals on desktop/mobile. Bind assertions to graph routes and changed PR code; record environment and test artifacts.
3. **Turn draft evaluations into a release signal:** add a broader independently reviewed PR and retrieval dataset, score grounding/precision/recall/faithfulness/relevance over multiple cases, calibrate confidence and add CI gates only after stable baselines.

Before any production launch, also complete deployment hardening, DLP/PII and retention controls, reliability/load testing, monitoring and incident procedures.

## 12. Repository map and review artifacts

- `ingestion/`: independent loaders, parsers, chunkers, extractors, stores, validators and retrieval evaluation.
- `agents/`: webhook, LangGraph pipeline, GitHub/Qdrant/Neo4j/Playwright adapters, guardrails, report formatting, run history and agent evaluation.
- `agents/evaluation/agent_quality/snapshots/2026-10-05-saleor-pr-1/`: exact saved live report and machine-readable judge results.
- `ingestion/docs/current-ingestion-run.md`: ingestion history and current graph refresh caveats.

Use [the sample QA report](sample-pr-impact-report.md) to see how the current output should be interpreted, and [the demo runbook](demo-runbook.md) to walk through the proof and its limits.
