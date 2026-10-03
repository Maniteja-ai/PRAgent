# PR impact report

Status: COMPLETED

## Observed behavioral checks

Verification execution: COMPLETED (separate from analysis findings).

| Environment | Check | Result |
| --- | --- | --- |
| baseline | Empty voucher disables Apply | PASS |
| baseline | Eligible voucher reaches backend | FAIL |
| baseline | Displayed discounted total and voucher state | FAIL |
| patched | Empty voucher disables Apply | PASS |
| patched | Eligible voucher reaches backend | PASS |
| patched | Displayed discounted total and voucher state | PASS |
| patched | Remove voucher and restore total | PASS |

Both URLs attested their configured source revisions and the same backend identity. The observed before/after difference supports attribution for the checks that ran.

Not run: Invalid voucher, Shipping prerequisites, Quantity changes, Baseline removal, Order submission

## Checkout Promo Code Application and Removal UI Support

PR 1199 replaces the static mock discount state in OrderSummary with functional GraphQL mutations (`useCheckoutAddPromoCodeMutation` and `useCheckoutRemovePromoCodeMutation`). It introduces input handling, error state rendering, and refetching logic upon successful mutation application or removal. Evidence includes the git diff and the verified deployed storefront UI elements.

Classification: potential impact; behavior is not verified.

Evidence: diff:83f02ceb3a841c99664d, attestation:patched:3e8dd01be34548c919d3, graph:c21865847098ad914df6

- Proposed check (not run): Apply the configured sandbox voucher in the patched checkout view and verify that the discount is correctly added to the order summary.
- Proposed check (not run): Remove the applied voucher in the patched checkout view and verify that the summary updates accordingly without submitting any orders or payments.

## Limits and gaps

- PR number and replay status are configuration labels; remote PR metadata was not verified.
- UI links have static structural validation and an attested deployed revision; behavior is reported separately.
- Only the explicitly listed behavioral checks were run; unlisted flows are outside scope.
- Reviewed requirement contracts passed for this scenario; other retrieved requirements remain unvalidated.

## Call usage

| Agent | Tool | Attempts |
| --- | --- | --- |
| coordinator | browser.act | 5 |
| coordinator | browser.check | 5 |
| coordinator | browser.navigate | 4 |
| coordinator | deployment.attest | 2 |
| coordinator | fixture.apply | 1 |
| coordinator | fixture.catalog | 1 |
| coordinator | fixture.observe | 3 |
| coordinator | fixture.prepare | 3 |
| coordinator | github.diff | 1 |
| coordinator | knowledge.documents | 1 |
| coordinator | knowledge.graph | 1 |
| coordinator | model.decide | 1 |
