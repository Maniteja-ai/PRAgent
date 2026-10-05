# Sample PR impact report — Saleor checkout voucher

**Pull request:** [Maniteja-ai/storefront#1](https://github.com/Maniteja-ai/storefront/pull/1)  
**Analysis run:** `live-e2e-pr1-scope-gap-20261005`  
**Run status:** **Completed with gaps**  
**Audience:** QA and product reviewers

## What changed

The checkout order summary now sends promo-code changes to Saleor instead of simulating a local `saleor10` code. It trims the entered value, displays API/validation errors, and refreshes checkout data after a successful change. When a voucher is already applied, the summary displays it and offers a remove action. The checkout page supplies that refresh behavior to both desktop and mobile summaries.

## What this could affect

| Customer flow | Possible impact | Evidence basis |
| --- | --- | --- |
| Apply a discount code at checkout | The code is submitted through Saleor's `checkoutAddPromoCode` mutation. Empty/trimmed input, pending state and returned errors affect the checkout form. | PR diff for `order-summary.tsx`; GraphQL mutation definition |
| View or remove an applied voucher | An applied voucher changes the summary from an input form to a code/discount display with a remove action. | PR diff for `order-summary.tsx` |
| See updated checkout totals | After apply/remove succeeds, a network-only checkout refetch is requested in both desktop and mobile layouts. | PR diff for `saleor-checkout.tsx` and `order-summary.tsx` |

These are code-impact predictions from the PR diff. They explain the behavior the code implements; they are not claims that the live checkout completed these flows successfully.

## What the automated browser check actually did

The configured Playwright journey added a product, navigated to checkout, and checked:

- the checkout URL includes a checkout identifier;
- the “Discount code” input is visible;
- the “Apply” button is visible.

All three configured assertions passed. The run **did not** submit a voucher, test an invalid code, remove an applied voucher, inspect the resulting error message, or compare checkout totals. The behavior result is a smoke-test PASS for those three checks only.

## QA follow-up recommended

1. Apply a valid voucher and confirm the summary displays the discount and refreshed total.
2. Apply an invalid or ineligible voucher and confirm the error is understandable and the total remains correct.
3. Remove an applied voucher and confirm the input returns and the total is refreshed.
4. Repeat on desktop and mobile layouts; include a request failure/retry case.

## Evidence and confidence

The findings are tied to the PR diff and related GraphQL/graph evidence in the saved report. The implementation details are well supported by the changed code. Runtime behavior for voucher submission and removal remains unverified by the browser run, which is why the report is `COMPLETED_WITH_GAPS`.

The LLM-judge snapshot reports 100% precision, recall, F1 and faithfulness, and 56.2% evidence relevance. Treat these as **diagnostic only**: they come from one PR case, one model judge, and a golden dataset still labeled `DRAFT_PENDING_HUMAN_REVIEW`. They are not an independent quality guarantee.

## Traceability

- Full generated report: [`report.md`](../../agents/evaluation/agent_quality/snapshots/2026-10-05-saleor-pr-1/report.md)
- Machine-readable evaluation: [`evaluation.json`](../../agents/evaluation/agent_quality/snapshots/2026-10-05-saleor-pr-1/evaluation.json)
- Evaluation notes and reproduction command: [`summary.md`](../../agents/evaluation/agent_quality/snapshots/2026-10-05-saleor-pr-1/summary.md)
