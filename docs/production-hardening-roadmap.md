# Production hardening roadmap

Status: approved backlog after the first complete Saleor voucher evaluation.

Implementation update (2026-10-03):

| Work item | Implemented | Remaining external or scope work |
| --- | --- | --- |
| Real-PR evaluation | Six frozen PRs, four development/two held out, deterministic relevance/citation/faithfulness scorer | Independent human label review; grow to 12 cases; blind coordinator predictions |
| Artifact DLP | Pluggable baseline and Google DLP scanners, scan-before-write, image/text support, fail-closed policy and value-free audit | Organization credentials, provider authorization and retention policy |
| GitHub integration | Signed/allowlisted webhook, SQLite dedupe/supersession, exact-head recheck, GitHub App tokens and idempotent comments | Install the App and enable comment publishing after deployment |
| Behavior scenarios | Live API oracles for invalid voucher, quantity change and shipping prerequisites | Approved baseline/patched browser scenarios for these three cases |

The current system has one attested Saleor PR scenario, four coordinator contract cases,
four live LLM safety cases, a 40-query retrieval dataset and bounded 100-run stability
experiments. The following work expands the evidence behind the production claim.

## 1. Independently reviewed real-PR evaluation

Build `coordinator-real-pr-v2` from real pull requests whose base and head commits remain
available. Keep development and held-out cases separate. The person or model implementing
the coordinator must not approve the reference labels.

Each case records:

- repository, PR number, immutable base/head SHAs and deployed build IDs when available;
- changed files and expected code relationships;
- expected affected UI elements, user flows and requirements;
- expected unmapped or unsupported claims;
- required citations and acceptable alternative evidence paths;
- author, independent reviewer, review date and approval state.

The first release target is at least 12 PRs: positive UI changes, backend-only changes,
renames/deletions, unmapped code, no-op changes and changes with no requirement coverage.
At least three cases stay held out until thresholds are selected. Report TP, FP and FN for
UI elements, flows and requirements, plus citation validity, abstention correctness and
complete-run rate. CI must refuse `APPROVED` when the author and reviewer identities match,
when a source SHA changes or when held-out labels are exposed to the workflow.

Completion requires a human reviewer who is independent of this implementation. Until that
review occurs, new labels remain `DRAFT` and cannot raise the release status.

## 2. DLP/PII at the artifact boundary

Keep the current deterministic model guardrails as a fast first layer. Add an
`ArtifactScanner` interface at the only artifact-write boundary and provide an adapter for an
organization-approved DLP service. JSON selects a registered provider and secret-manager
reference; it cannot import arbitrary code.

The write path becomes:

```text
bytes -> media/type validation -> DLP scan -> allow, redact, or quarantine -> atomic hash write
```

Required behavior:

- scan text, JSON, HTML and OCR-supported images before persistent storage;
- use provider findings for names, addresses, national identifiers, health data and secrets;
- block or quarantine on scanner timeout in production mode;
- never send credentials, cookies or checkout tokens to a third-party scanner;
- store category/count/policy/version in the audit event, never the sensitive value;
- scan again before retrieval or model use to protect older artifacts;
- encrypt quarantined objects, restrict access and apply retention/deletion policy;
- test false positives, false negatives, Unicode text, images, provider timeouts and replay.

Completion requires the organization to choose and authorize a DLP provider and retention
policy. A regex-only detector must continue to identify itself as baseline protection.

## 3. GitHub webhook and PR reporting

Run this integration as a small boundary service around the coordinator. Use a GitHub App
with repository-scoped installation tokens rather than a personal access token.

```text
pull_request webhook
  -> verify signature and delivery ID
  -> accept opened, reopened, synchronize or ready_for_review
  -> persist an idempotent job with repository, PR and head SHA
  -> run the coordinator against that exact SHA
  -> re-check the PR head
  -> publish or update one Check Run / PR comment
```

The service must reject invalid signatures before parsing work, deduplicate GitHub delivery
IDs, ignore unsupported repositories/actions and cancel superseded head SHAs. Comment text is
rendered from the validated JSON report and includes status, affected flows, citations,
verification results and limitations. Publishing uses a separate five-attempt ledger entry and
an idempotency key; analysis remains useful when publishing fails.

Minimum GitHub App permissions are read-only metadata/contents/pull requests plus write access
only to Checks or Issues when report publication is enabled. Webhook payloads and PR text are
untrusted data. Secrets never enter checkpoints, reports or comments.

## 4. Additional behavioral scenarios

Implement each scenario behind `BehaviorScenario` with its own JSON, requirement contracts,
fixture namespace, run ID and call budget. Do not append every check to the existing voucher
scenario: it already uses five browser actions and five browser checks.

| Scenario | Required observations | Safety boundary |
| --- | --- | --- |
| Invalid voucher | UI rejects code; backend leaves total and voucher state unchanged | Dedicated guest cart; no retry of mutation after uncertain result |
| Shipping prerequisite | Shipping voucher rejected before address/method, accepted afterward, shipping total becomes zero | Sandbox address and method only; no payment or order submission |
| Quantity change | Quantity update reaches backend and UI totals recalculate while voucher remains correct | Stock checked first; bounded quantity 1-5 |
| Voucher removal | Applied voucher removed and original total restored | Already implemented; retain as regression control |

Each scenario needs baseline and patched observations where the UI exists, a backend oracle,
runtime build attestation, content-addressed screenshots/DOM/assertions, and explicit `PASS`,
`FAIL`, `BLOCKED` or `NOT_RUN` outcomes. Scenario selection must be unambiguous; overlapping
changed-path rules block execution until a narrower policy is chosen.

## Delivery order

1. Prepare the real-PR dataset template and reviewer workflow; collect cases in parallel with
   engineering work, but do not call them gold before independent approval.
2. Add the artifact-scanner interface and organization-selected DLP adapter before accepting
   webhook traffic.
3. Add invalid-voucher and quantity scenarios, then shipping prerequisites.
4. Add the GitHub App webhook boundary in report-only mode; enable PR publication after its
   signature, idempotency and permission tests pass.
5. Rerun the final campaign with held-out PRs and update release thresholds from measured data.
