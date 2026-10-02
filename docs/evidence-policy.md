# Evidence and evaluation policy

## Keep the requirement source independent

The selected PR explains the defect and is useful for a reviewer-authored evaluation oracle. It must not be treated as the independent source of product requirements. The requirements package instead references the pinned storefront README and official backend/API documentation.

The eventual ingestion agent must parse those sources itself. This manually curated package is reference material for review and evaluation; do not report it as automated extraction.

## Separate supported claims

- A documented frontend capability supports a UI requirement.
- A backend API capability establishes backend behavior, not proof that every storefront exposes it.
- A UI acceptance criterion inferred from an API contract is labeled explicitly.
- Source inspection is code evidence, not proof that a browser flow works.
- A successful deployment build is not proof that checkout or vouchers work.

Record coverage as NOT_EVALUATED, OBSERVED, NOT_OBSERVED, or BLOCKED with crawl/run context. Presence of a form is not functional coverage. Do not describe NOT_OBSERVED as confirmed missing.

## Avoid contaminating the impact analysis

Build the requirements/UI/code graph at the baseline revision. Keep the manually reviewed PR expectations separate from the graph inputs. Then feed the real diff to the analyzer. Use the patched deployment only to validate predictions afterwards. The later agent may read the PR description but must disclose whether it used it; also evaluate a diff-only mode.

## Repeatability

For each run record baseline and patched SHA, backend version, document retrieval time/hash, fixtures, browser version, fresh session/checkout IDs, model/version, prompts, action budget, and artifacts. The shared backend requires separate fresh carts for the two deployments; never reuse browser storage as a substitute for a clean comparison.

Confidence values, when added by the future agent, are heuristic scores until calibrated against labeled examples. Do not assign arbitrary numeric certainty to these manually prepared requirements.

