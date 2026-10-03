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

| File | Purpose |
| --- | --- |
| [saleor-verified.json](configs/saleor-verified.json) | Execution limits, model, checkpoint directory, approval and scenario selection |
| [saleor-application.json](configs/saleor-application.json) | Repository, pinned commits, graph/vector inputs, deployments and browser permissions |
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

Use [saleor-live.json](configs/saleor-live.json) only for open-ended UI discovery. Its exploration
agent shares the same five-call model ceiling as final analysis. The production profile disables
discovery because reviewed UI mappings already exist and preserves the budget for deterministic
verification.

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

The supplied scenario is explicitly `preapproved`. The report binds approval to the selected
scenario and a SHA-256 policy fingerprint. For a human-reviewed scenario, set
`"approval": "human_review"`, inspect the saved plan, and resume with `--review` and a JSON reply.

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
Its four reviewed cases cover a cited impact, citation repair, absence of a supported finding,
and a bounded retrieval loop. Latest metrics are 2 true positives, 0 false positives, 0 false
negatives, precision 1.0, recall 1.0, and 4/4 cases passed. These figures describe this reviewed
dataset, not arbitrary repositories.

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
- test suite: **256 passed**, **90.15% combined statement/branch coverage**;
- replay: the same run ID produced a byte-identical report with no new calls;
- Ruff lint and formatting checks passed.

## Package structure

| Module | Responsibility |
| --- | --- |
| `application.py`, `config.py` | Strict application/execution models and schemas |
| `workflow.py`, `state.py` | LangGraph nodes, routing and report construction |
| `runtime.py`, `ledger.py` | Guarded dispatch, quotas, receipts and recovery |
| `adapters/local_git.py`, `adapters/git_changes.py` | Immutable local change evidence |
| `adapters/attestation.py` | Deployed revision/backend verification |
| `adapters/knowledge.py` | Neo4j and vector retrieval bridge |
| `adapters/browser.py` | Restricted Playwright observations and actions |
| `mapping.py` | Validate and publish code/UI/flow graph mappings |
| `verification.py`, `verification_stage.py` | Requirements, approval, selection and deterministic checks |
| `adapters/voucher_verification.py` | Saleor fixture and browser implementation |
| `evaluation.py` | Reviewed coordinator golden evaluation |
| `real_pr_evaluation.py` | Real-PR relevance, citation and faithfulness scoring |
| `artifact_security.py` | Pluggable artifact DLP enforcement and audit |
| `github_webhook.py` | Signed webhook intake, durable jobs, GitHub App auth and PR comments |
| `additional_behavior.py` | Bounded Saleor API behavior oracles |
| `campaign.py` | Cross-system evaluation gates and honest scope classification |
| `readiness.py` | Machine-readable production release gate |
| `api.py`, `bootstrap.py`, `cli.py` | Public API, adapter construction and commands |

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
