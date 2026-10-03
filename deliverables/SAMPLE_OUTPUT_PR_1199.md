# QA impact brief: Saleor Storefront PR 1199

**Decision:** Focus checkout regression testing on voucher application, displayed totals, removal, and errors.
**Observed result:** The patched build supports voucher application and removal. The baseline build displays the controls but does not apply the eligible voucher.
**Run:** `submission-pr-1199-02`
**Scope:** guest checkout; no order or payment submitted

## What changed

PR 1199 replaces placeholder promo-code state in the checkout order summary with Saleor GraphQL operations that add and remove a voucher. It also adds loading and error handling and refreshes checkout data after the mutation succeeds.

Changed code:

- `src/checkout/views/saleor-checkout/order-summary.tsx`
- `src/checkout/views/saleor-checkout/saleor-checkout.tsx`

## Product areas at risk

| Area | Why it is connected to the change | Evidence strength |
| --- | --- | --- |
| Discount-code input | The changed component owns the input state and add-voucher request | Diff plus observed UI |
| Apply button | The changed handler submits the configured code and exposes loading state | Diff plus observed action |
| Applied-voucher summary | Checkout data must render the active voucher and discount | Backend and UI observation |
| Remove-discount control | The new remove mutation must restore the undiscounted checkout | Diff plus verified action |
| Subtotal and total | Applying or removing a voucher changes the amount shown to the shopper | Verified baseline/patched comparison |
| Inline error message | Mutation failures are rendered in the order summary | Diff; invalid-code backend behavior tested separately |

## Affected user flows

1. Add a product to a guest cart and open checkout.
2. Enter a valid discount code and select **Apply**.
3. Confirm the voucher and discounted total appear.
4. Remove the voucher and confirm the original total returns.

Nearby flows tested separately:

- invalid voucher rejection;
- quantity change while a voucher is active;
- shipping voucher before and after address prerequisites.

## Verified behavior

| Check | Baseline | Patched |
| --- | --- | --- |
| Empty code keeps Apply disabled | Pass | Pass |
| Eligible voucher reaches the backend | Fail | Pass |
| Discounted total and voucher state appear | Fail | Pass |
| Remove voucher and restore total | Not in selected baseline scenario | Pass |

The fresh patched checkout changed from **USD 16.00** to **USD 14.40** after applying `TSIGMA10`, then returned to **USD 16.00** after removal. Both deployed URLs attested their configured revisions and used the same Saleor backend, which supports attributing the observed difference to the compared storefront builds.

The three additional fresh-checkout scenarios also passed:

- invalid code rejected and checkout unchanged;
- quantity updated, voucher remained active, and total recalculated;
- shipping voucher rejected before prerequisites and applied after prerequisites were set.

## Requirements affected

| Requirement | Assessment |
| --- | --- |
| Applying an eligible voucher updates the checkout backend and displayed total | Verified on patched build |
| Removing an applied voucher restores the original checkout total | Verified on patched build |
| Invalid vouchers leave checkout totals unchanged | Verified by separate behavior oracle |
| Voucher remains valid when item quantity changes | Verified by separate behavior oracle |
| Shipping-specific vouchers require shipping prerequisites | Verified by separate behavior oracle |

## Recommended QA checks

Run the voucher scenario on every checkout change that touches order summary state, checkout refetching, or voucher mutations. Keep these assertions:

- Apply is disabled for an empty code.
- A valid voucher changes backend state and the displayed total.
- The applied voucher is visible and removable.
- Removal restores the original total.
- An invalid code does not change checkout state.
- Quantity changes recalculate the discounted total.
- Shipping-specific discounts respect address prerequisites.

## Limits

- This is a historical PR replay; remote PR metadata is not required for the local diff and was not used as proof.
- Literal labels and static code relationships identify structural code-to-UI candidates. Runtime component attribution is not yet instrumented.
- The run did not submit an order, execute payment, or cover authenticated customer checkout.
- Requirement extraction is source grounded, but the complete requirement corpus has not received independent semantic review.

## Evidence

- Live baseline: <https://testsigma-saleor-baseline.vercel.app>
- Live patched build: <https://testsigma-saleor-patched.vercel.app>
- Raw machine report: `packages/coordinator/artifacts/submission-pr-1199-02/report.json`
- Generated report: `packages/coordinator/artifacts/submission-pr-1199-02/report.md`
- Evaluation campaign: `packages/coordinator/artifacts/final-evaluation-01/report.md`
