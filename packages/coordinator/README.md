# Trace coordinator

Trace coordinator is an independently installable LangGraph service for PR impact analysis.
It combines an immutable local Git diff, Neo4j code/UI traversal, vector document retrieval,
bounded model reasoning, runtime build attestation, and deterministic browser verification.

The current release is **production-ready for the configured Saleor PR 1199 sandbox scope**.
The machine-readable [release gate](artifacts/production-readiness-02/report.md) is `READY`.
Adding another repository or user flow requires its own reviewed graph, requirement contracts,
runtime attestation, deterministic verifier, and golden cases.

## What the production workflow does

```text
validated JSON request
  -> immutable local Git diff
  -> attest baseline and patched deployments
  -> retrieve code/UI paths from Neo4j
  -> retrieve related documents from Qdrant
  -> one bounded model analysis
  -> validate and normalize every citation
  -> select an approved deterministic scenario
  -> verify baseline and patched behavior
  -> write a hash-verifiable report and audit ledger
```

The model identifies potential impact. It cannot invent executable tests or invoke fixture
operations directly. Deterministic checks decide PASS, FAIL, BLOCKED, or NOT_RUN. A completed
analysis is called complete for configured scope only when it has a cited finding, both deployed
builds attest successfully, and configured behavior verification completes.

## JSON configuration

See the one-page [configuration map](configs/README.md) for the complete file flow and a simple
description of every configuration group.

| File | Purpose |
| --- | --- |
| [saleor-verified.json](configs/saleor-verified.json) | Model, application, runtime policy, review and verification selection |
| [application/saleor.json](configs/application/saleor.json) | Repository and baseline/patched deployments |
| [graph/saleor.json](configs/graph/saleor.json) | Neo4j graph snapshots |
| [retrieval/saleor.json](configs/retrieval/saleor.json) | Ingestion run, vector store and ranking stages |
| [ui/saleor.json](configs/ui/saleor.json) | Playwright startup and allowed UI actions |
| [runtime-defaults.json](src/trace_coordinator/resources/runtime-defaults.json) | One source for call, retry, timeout and guardrail defaults |
| [standard.json](configs/runtime/standard.json) | Only the operational values this deployment overrides |
| [saleor-policy.json](configs/verification/saleor-policy.json) | Approved verification scenarios |
| [saleor-voucher.json](configs/verification/saleor-voucher.json) | Voucher fixtures, UI controls, assertions and requirement contracts |
| [real-pr-v2](evaluation/real-pr-v2/README.md) | Frozen real-PR ground truth, review process and metric definitions |
| [saleor.json](configs/webhook/saleor.json) | Signed webhook intake, durable queue and optional PR comment |
| [google-dlp.example.json](configs/security/google-dlp.example.json) | Fail-closed Google DLP policy fragment for stored artifacts |
| [saleor-request.json](examples/saleor-request.json) | Project, PR label and analysis question |
| [production-gate.json](configs/release/production-gate.json) | Required live outcomes, test count, coverage and evaluation gates |

All models reject unknown fields. `$schema` links each editable JSON file to a generated JSON
Schema, so an editor can provide completion, allowed values, descriptions and validation.
Credentials are read from named environment variables through the referenced `.env`; they are
never stored in these files or reports.

`application_config_file` selects the application. That file points to the graph, retrieval and UI
files. `runtime_config_file` contains optional operational overrides; omitting it applies the same
safe typed defaults. `ui_exploration` controls bounded UI discovery before analysis.

The packaged `runtime-defaults.json` is the single source for call limits, retries, timeouts and
guardrails. Runtime files contain only deployment-specific overrides. The same values are
published in `runtime.schema.json`, so JSON-aware editors show them without copying them into every
config.

## Optional LangSmith observability

The coordinator can send the LangGraph execution tree, node timing and safe run coordinates to
LangSmith. SQLite checkpoints, the call ledger, evidence hashes and generated reports remain the
authoritative audit record. LangSmith is an optional visualization layer and an outage never
changes the analysis or verification result.

`saleor-live.json` enables the provider with safe defaults:

```json
"observability": {
  "provider": "langsmith",
  "project": "testsigma-impact-agent-demo",
  "api_key_env": "LANGSMITH_API_KEY",
  "dashboard_url": "https://smith.langchain.com",
  "capture_content": false,
  "sampling_rate": 1.0,
  "flush_timeout_seconds": 2.0,
  "request_timeout_seconds": 1.0
}
```

Put `LANGSMITH_API_KEY` in the referenced `.env` file and run the normal coordinator command. The
report contains the project, root trace ID, dashboard link and local observability audit event.
No global tracing environment switch is required because the callback is attached only to this
graph invocation. Inputs and outputs are forcibly hidden, so PR code, retrieved documents, DOM,
questions and review answers are excluded. The schema rejects `capture_content: true`.

If the key is absent, initialization fails or trace delivery is unavailable, the report records
`UNAVAILABLE` or best-effort delivery and the coordinator continues. Provider requests use no
automatic retries and a short configurable timeout. Set the provider to
`{"provider":"disabled"}` to make no LangSmith client at all. `endpoint_env` and
`workspace_id_env` support regional, self-hosted and multi-workspace setups without putting those
values directly in the execution JSON.

Use [saleor-live.json](configs/saleor-live.json) only for open-ended UI discovery. Its UI
exploration stage shares the same five-call model ceiling as final analysis. The production profile
disables UI exploration because reviewed UI mappings already exist and preserves the budget for
deterministic verification.

## Run it

From this package directory:

```powershell
uv sync --locked --extra gemini --extra knowledge --extra browser
uv run --no-sync playwright install chromium

uv run --no-sync trace-coordinator run `
  configs/saleor-verified.json `
  examples/saleor-request.json `
  --run-id my-saleor-analysis `
  --output artifacts/my-saleor-analysis
```

Run IDs are immutable. Reusing the same run ID with the same request and configuration replays
the saved result without external calls. A different request, provider fingerprint, policy, or
configuration under that ID is rejected.

Human review behavior is explicit JSON configuration:

```json
"human_review": {
  "policy": "non_blocking",
  "allow_follow_up_verification": true
}
```

`blocking` uses a durable LangGraph interrupt and returns `WAITING_FOR_REVIEW`. `non_blocking`
records the exact question, marks approval-dependent checks `NOT_EXECUTED`, and returns
`COMPLETED_WITH_GAPS` immediately. A later `--review` response against the original run ID creates
a deterministic linked verification run. It starts from the saved verification boundary, retains
the original evidence and hashes, and charges every new verification call to the original ledger.
It cannot replenish per-tool, total-call, review, retry, or active-execution time limits; only the
offline human-response interval is excluded from elapsed execution time.

The verified Saleor profile uses local Git objects. It does not need a GitHub token or API call.
The PR number and historical replay flag are labels; the adapter proves that the selected
upstream patch is byte-identical to the source difference between the two deployed revisions.

## Runtime attestation

Production mode requires HTTPS deployments with a same-origin `/api/trace-build` endpoint.
The endpoint returns the running Git revision, deployment ID, channel, and a SHA-256 fingerprint
of the Saleor GraphQL URL. The coordinator rejects redirects, oversized or invalid responses,
wrong revisions, and baseline/patched backend mismatches before retrieval or model reasoning.

The current deployments attest:

| Environment | Git revision | Deployment |
| --- | --- | --- |
| Baseline | `1b5d6545d34fda38c1fb24712ed4bf3dffc684b8` | `dpl_GxxZ986iq19KvrPtzmMXeL5zAtqD` |
| Patched | `f1f0abe96cd9adabc28fe2bb12d407dfef171379` | `dpl_5pF62jQUsqeCkV9W1zA7cCT7m7sh` |

Both reported the same backend fingerprint and `default-channel`. The endpoint sets
`Cache-Control: no-store`.

## Retrieval and evidence

The graph retriever follows changed files and symbols to components, UI controls, user flows,
and requirements. The vector retriever supplies relevant public documentation. Graph and vector
results remain separate evidence types; the coordinator does not turn document similarity into
a code dependency.

Every finding must cite known evidence IDs. Nested UI, flow, or requirement IDs can resolve only
through a unique structured graph result. Unknown and ambiguous citations are rejected. Saved
diffs, graph results, documents, screenshots, transitions and verification observations carry
their path, byte count and SHA-256. The release gate reads and verifies every artifact again.

The head graph contains reviewed structural links from `OrderSummary` to the **Discount code**
field and **Apply** button, plus the product-to-checkout flow. These mappings establish structural
impact. The deterministic scenario establishes behavior.

## Model guardrails and live evaluation

Every normal coordinator model call uses the same deterministic boundary policy. Sensitive data
in the user question or human review is blocked before persistence or provider dispatch. Email,
formatted phone, SSN, payment-card, common API-key, bearer-token and JWT patterns in evidence are
redacted before the model. Prompt-injection phrases in untrusted evidence are quarantined as one
marker. Model output is accepted only after strict schema and sensitive-output validation; tool
allowlists, argument schemas and citation grounding are enforced by the coordinator afterward.

The report records input/output guardrail events with category counts and a policy fingerprint.
Detected values are never placed in those events. Regex detection is a conservative baseline,
not a substitute for an organization-specific DLP service or multilingual PII classifier.

Stored artifacts can additionally use the `google_dlp` provider. It scans text and images before
the content-addressed write, blocks sensitive findings, scanner failures and unsupported types,
and stores only finding categories/counts plus hashes in the audit log. Install `--extra dlp`, set
`GOOGLE_CLOUD_PROJECT` and Application Default Credentials, then copy the `artifact_security`
fragment from [google-dlp.example.json](configs/security/google-dlp.example.json) into the
coordinator JSON. The default remains disabled so a local run cannot claim that cloud DLP ran.

Run the live suite with:

```powershell
uv run --no-sync trace-coordinator llm-evaluate `
  evaluation/live-llm-saleor-v1.json `
  --output artifacts/live-llm-evaluation
```

The reviewed suite makes at most five provider calls. It covers a grounded answer, evidence PII
redaction, prompt-injection quarantine, and a sensitive user request blocked without a provider
call. The latest Gemini execution passed 4/4 cases with three live calls, 100% structured output,
100% grounded findings, zero sensitive-output leaks, 1.50-second median latency and 2.04-second
maximum latency. These are four controlled cases, not a broad safety certification.

## Verification and approval

The Saleor scenario creates three temporary sandbox guest carts and submits no order or payment.
It checks the voucher in the backend, then verifies browser-visible behavior against the backend
oracle. Mutation retries are disabled. An interrupted mutation remains uncertain and requires
reconciliation; it is never repeated silently.

The supplied scenario requires `human_review` and uses the non-blocking policy. The first run saves
the question and verification plan without executing it. To approve later, create a file such as
`review.json` containing `{"answer":"approve"}` and rerun the same command and run ID with
`--review review.json`. Only an exact `approve` authorizes the configured scenario. Any other answer
creates a linked `COMPLETED_WITH_GAPS` run with verification still `NOT_EXECUTED`. The report binds
the answer, parent run, original evidence hash, scenario and policy fingerprint in the audit trail.

Requirement contracts are data, not hard-coded workflow logic:

| Requirement | Required checks | Latest result |
| --- | --- | --- |
| `VOUCHER_APPLY_TOTAL` | backend accepted voucher; displayed total and voucher state match | PASS |
| `VOUCHER_REMOVE_TOTAL` | removal restores the original total | PASS |

The final live report observed:

| Check | Baseline | Patched |
| --- | --- | --- |
| Empty input disables Apply | PASS | PASS |
| Eligible voucher reaches backend | FAIL | PASS |
| Discounted total and voucher state | FAIL | PASS |
| Remove voucher and restore total | Outside baseline scope | PASS |

Because both source revisions and the shared backend were attested, the report marks this
before/after comparison `SUPPORTED` for the checks that ran.

## Bounded execution and recovery

Every external operation passes through one dispatcher. The quota key is
`(run_id, agent_id, canonical_tool_name)`. The hard per-tool maximum is five attempts. Changed
arguments, failures, and counted retries do not reset it. A total-call ceiling, model-round limit,
review limit, validation-repair limit, and wall-clock deadline add independent stops.

SQLite transactions reserve attempts before execution. A run lock prevents concurrent execution
of one run. Completed calls are cached. If a process dies after a reservation or side effect but
before its receipt is committed, that attempt remains consumed and uncertain, and the workflow
fails closed.

Browser actions use exact configured names or observed `data-testid` values, stay on the configured
origin, and require a fresh observation. Baseline and patched use isolated guest contexts.

## Evaluation and release gate

Run the reviewed golden dataset and gate with:

```powershell
uv run --no-sync trace-coordinator evaluate `
  evaluation/coordinator-golden-v1.json `
  --output artifacts/golden-evaluation

uv run --no-sync trace-coordinator evaluate-stability `
  evaluation/coordinator-golden-v1.json `
  --repetitions 100 `
  --output artifacts/coordinator-stability

uv run --no-sync trace-coordinator evaluation-campaign `
  configs/evaluation/final-campaign.json `
  --output artifacts/final-evaluation

uv run --no-sync trace-coordinator release-check `
  configs/release/production-gate.json `
  --output artifacts/production-readiness
```

The golden evaluator executes the complete coordinator contract with deterministic adapters.
Its five reviewed cases cover a cited impact, citation repair, absence of a supported finding,
a bounded retrieval loop, and non-blocking human review. Latest metrics are 2 true positives,
0 false positives, 0 false negatives, precision 1.0, recall 1.0, and 5/5 cases passed. These
figures describe this reviewed dataset, not arbitrary repositories.

The [six-case real-PR dataset](evaluation/real-pr-v2/README.md) scores exact UI/flow/requirement
relevance, citation validity, evidence-based faithfulness and claim recall. Its current labels and
candidate predictions were authored in the same implementation pass, so the 1.0 scorer smoke
result is `DRAFT_EVALUATED` and cannot gate a release. A second human must approve the labels, and
a blind coordinator run must generate new predictions.

Run the three additional sandbox API oracles with unique run IDs:

```powershell
uv run --no-sync trace-coordinator verify-additional configs/verification/saleor-invalid-voucher.json --run-id invalid-01 --output artifacts/invalid-01
uv run --no-sync trace-coordinator verify-additional configs/verification/saleor-quantity-change.json --run-id quantity-01 --output artifacts/quantity-01
uv run --no-sync trace-coordinator verify-additional configs/verification/saleor-shipping-prerequisite.json --run-id shipping-01 --output artifacts/shipping-01
```

They create guest checkouts, never submit orders or payments, and never retry mutations. They are
backend behavior oracles for invalid codes, quantity changes and shipping prerequisites. UI
rendering for those cases requires a separately approved browser scenario.

## GitHub webhook and PR comment

The endpoint verifies `X-Hub-Signature-256` before parsing JSON, allowlists repositories and
actions, limits payload size, deduplicates deliveries in SQLite and supersedes older queued heads.
Before analysis, the worker uses a short-lived GitHub App installation token to confirm that the
queued revisions are still current. Comment publication is disabled in the sample; setting
`publish` to `comment` updates one marker comment or creates it once.

```powershell
uv sync --locked --extra webhook --extra gemini --extra knowledge --extra browser
$env:GITHUB_WEBHOOK_SECRET = "..."
$env:GITHUB_APP_ID = "..."
$env:GITHUB_APP_PRIVATE_KEY_FILE = "C:\\secure\\path\\github-app.pem"
uv run --no-sync trace-coordinator webhook-serve configs/webhook/saleor.json
uv run --no-sync trace-coordinator webhook-run-once configs/webhook/saleor.json
```

The sample retains the two pinned Saleor deployments. A PR whose patch differs from those builds
fails closed; new base/head deployments and their attestation config are required before making
behavioral claims.

The stability command repeats that complete contract up to 100 times and compares a normalized
behavioral output that excludes run paths and timing. The latest run passed 100/100 attempts with
one distinct behavioral output. The final campaign reads immutable reports through one strict JSON
configuration and applies thresholds across ingestion, vector retrieval, the optional selector,
Neo4j traversal, coordinator behavior, live LLM safety and the verified browser scenario. Its
`PASSED_WITH_LIMITATIONS` status means every configured machine check passed while provisional
labels and narrow scenario coverage remain visible in the report.

The release gate fails closed unless all of these hold:

- workflow and configured scope completed;
- two exact runtime attestations share a backend;
- approval is bound to a scenario and policy fingerprint;
- declared browser checks and requirement contracts have their expected outcomes;
- model guardrails ran on the production workflow and the live LLM evaluation passed;
- deterministic coordinator golden evaluation passed;
- the complete test suite and coverage thresholds passed;
- every tool stayed within five attempts with no uncertain attempts or limit events;
- citations resolve and every evidence artifact matches its recorded hash and size.

Current evidence:

- [live analysis report](artifacts/saleor-production-09/report.md): completed, guarded, attested and verified;
- [live LLM evaluation](artifacts/live-llm-evaluation-01/report.md): passed;
- [golden evaluation](artifacts/golden-evaluation-03/report.md): passed;
- [100-run coordinator stability](artifacts/coordinator-stability-01/report.md): 100/100, one output;
- [final evaluation campaign](artifacts/final-evaluation-01/report.md): 17/17 checks passed with stated limits;
- [production release gate](artifacts/production-readiness-04/report.md): `READY`;
- current test suite: **268 passed**; Python 3.14/Windows measured **90.24%** combined
  statement/branch coverage, while Python 3.12/Linux measured **87.40%** because Playwright
  calls run in a worker thread; CI keeps every module in scope and enforces the portable **87%** floor;
- replay: the same run ID produced a byte-identical report with no new calls;
- Ruff lint/format, Mypy package checks and strict core-contract checks passed.

## Package structure

| Module | Responsibility |
| --- | --- |
| `domain/` | Pydantic domain models plus typed checkpoint, report and persistence contracts |
| `application/` | LangGraph workflow, guarded dispatch, verification and mapping use cases |
| `infrastructure/adapters/` | Git, GitHub, knowledge, browser, model and Saleor provider boundaries |
| `infrastructure/ledger.py` | Transactional quotas, receipts and recovery |
| `infrastructure/github_webhook.py` | Signed webhook intake, durable jobs and PR comments |
| `security/` | Model guardrails and pluggable artifact DLP enforcement |
| `evaluation/` | Golden, real-PR, live-model, campaign and release-gate evaluation |
| `presentation/` | CLI commands and deterministic Markdown rendering |
| `config.py`, `bootstrap.py` | Strict configuration schemas and dependency construction |

Plain JSON is limited to provider, webhook, checkpoint and artifact boundaries. Those values are
validated immediately into Pydantic models or named `TypedDict` contracts before application code
uses them. CI type-checks every source module and applies Mypy strict mode to domain contracts,
configuration, dispatch interfaces, the runtime and the call ledger.

## Add another repository or scenario

Keep the coordinator unchanged. Add a new strict application JSON, immutable repository inputs,
ingested documents, graph snapshots, deployments with attestation, and reviewed golden cases.
Implement a new deterministic scenario behind `ApprovedScenario` when the behavior differs.

New tools implement the `Tool` protocol: canonical name, version fingerprint, Pydantic input,
trusted agent allowlist, and one bounded `execute` operation. Register providers explicitly in
bootstrap. JSON never imports arbitrary Python. Provider SDK retries must remain disabled so all
attempts pass through the shared ledger.

The package can move to a separate repository. Replace the monorepo `tool.uv.sources` entry for
the optional `trace-impact` dependency with a published package or remote retrieval adapter; the
core package and fixture workflow do not depend on Neo4j, Playwright, or the ingestion library.
