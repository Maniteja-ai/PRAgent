# Agent quality evaluation: saleor-pr-1-voucher-impact

- Run: `live-e2e-pr1-scope-gap-20261005` (COMPLETED_WITH_GAPS)
- PR: [Maniteja-ai/storefront#1](https://github.com/Maniteja-ai/storefront/pull/1)
- Dataset: `saleor-pr-impact-quality-v1` (DRAFT_PENDING_HUMAN_REVIEW)
- LLM judge: `gemini-3.8-flash`; one judge request

## Scores

| Measure | Result | How to read it |
| --- | ---: | --- |
| Ground-truth precision | 100.0% | Report claims that match an expected impact |
| Ground-truth recall | 100.0% | Expected impacts mentioned in the report |
| Ground-truth F1 | 100.0% | Combined precision and recall |
| Faithfulness | 100.0% | Atomic claims supported by their cited evidence (normalized 0–1) |
| Claim relevance | 100.0% | Report claims relevant to this PR |
| Evidence relevance | 56.2% | Retrieved evidence relevant to this PR |

## Expected impact coverage

- **covered (2/2):** The checkout summary submits a trimmed code through Saleor checkoutAddPromoCode, handles transport and GraphQL errors, and on success clears the input and requests a checkout refresh. — The report accurately details the checkoutAddPromoCode GraphQL mutation call with trimmed promo code input, error state display from API and GraphQL errors, input disabling during mutation, and checkout refresh on success.
- **covered (2/2):** When checkout data contains an applied voucher, the summary displays its code and discount name; removal calls checkoutRemovePromoCode, handles errors, and refreshes checkout after success. — The report correctly explains that when checkout.voucherCode is present, a badge showing the code and discount name replaces the input form, and clicking remove executes checkoutRemovePromoCode with error handling and summary refresh.
- **covered (2/2):** The checkout page supplies the network-only checkout refresh callback to both mobile and desktop order summaries. — The report accurately covers passing the onCheckoutChange callback with a network-only refetch policy to both mobile and desktop OrderSummary component instances in SaleorCheckout.

## Claim checks

- **supported (2/2), relevance 2/2:** Shoppers redeeming promo codes in the checkout summary trigger the checkoutAddPromoCode GraphQL mutation rather than mock state. (apply-promo-code) — Directly supported by the OrderSummary diff replacing the mock 'saleor10' check with the useCheckoutAddPromoCodeMutation call.
- **supported (2/2), relevance 2/2:** The UI displays API and checkout validation error messages and disables input controls while the promo code mutation is in flight. (apply-promo-code) — Directly confirmed by the diff adding getCheckoutErrorMessage, promoError alert rendering, and disabled={isPromoBusy} attributes.
- **supported (2/2), relevance 2/2:** When an active voucher exists on checkout, the discount input form is replaced by a badge showing the applied code and discount name. (remove-applied-voucher) — Directly verified by the conditional rendering in order-summary.tsx checking appliedPromoCode to display the green badge.
- **supported (2/2), relevance 2/2:** The applied voucher badge provides a removal button that executes checkoutRemovePromoCode mutation. (remove-applied-voucher) — Directly supported by the handleRemovePromo function invoking removePromoCode upon clicking the removal button.
- **supported (2/2), relevance 2/2:** SaleorCheckout passes an onCheckoutChange callback to OrderSummary on both desktop and mobile viewports. (refresh-mobile-and-desktop-checkout) — Directly supported by the diff in saleor-checkout.tsx wiring onCheckoutChange to both OrderSummary components.
- **supported (2/2), relevance 2/2:** The onCheckoutChange callback invokes refetch with a network-only request policy to update totals, taxes, and shipping. (refresh-mobile-and-desktop-checkout, apply-promo-code, remove-applied-voucher) — Directly supported by the callback definition refetch({ requestPolicy: 'network-only' }) in saleor-checkout.tsx.

## Retrieved evidence relevance

- **1/2** `neo4j:89c4b5becd48c97277de2b4615bd2b3278ebb19dbfa509013533ff322e889f92` — Provides code graph relationships and route reachability for order-summary.tsx.
- **1/2** `neo4j:d02e46ac4aff541b8d5f4e8b1a30379558a33db138c0af47d5a0eb1079649026` — Provides code graph relationships and route reachability for saleor-checkout.tsx.
- **1/2** `27948db9d7277a3c9693f9fba07ed48bf370cd1d4bc7b024bde34f208b8b217d` — Shows base pre-change implementation of OrderSummary component and type definitions.
- **2/2** `c598135bac2d5a79a49b8956bd7033e372485535b5bb492cc4e56a78ac19d3bd` — Contains checkoutAddPromoCode and checkoutRemovePromoCode GraphQL mutation definitions used in the PR.
- **1/2** `f873df297f281d7cdf69ef67697160c3d4517e9cf06ba88a96d3f4a1bed609cd` — External documentation describing arguments for checkoutRemovePromoCode.
- **1/2** `a8f8ccaf7d00c8c79f279c85858ee48cc85dfe398c2764729ff4b1bdc0551b3c` — External documentation describing arguments for checkoutAddPromoCode.
- **1/2** `fbdbe43e8de5642d531c932a95ed0f8441bfa9d228a2db76b78a233a31461358` — External documentation showing example responses for applying vouchers.
- **0/2** `7cea8602af6e90010c08cf8267836f95f0e94015ac9633c5c630f1f040414f55` — External documentation page footer and copyright boilerplate with no relevance to PR functionality.
- **1/2** `1422c27b307593a8212669048de99097e6618c3fa42f885ab12c667567df500c` — GraphQL definition file showing related checkout schema fragments, though not promo code mutations directly.
- **1/2** `4cbf50520a770da3c04ec0b033ca5449119384ae05bcd76578a3b5455753f6a7` — External documentation illustrating error handling structure for checkoutAddPromoCode.
- **1/2** `5454f4ddc377d11d658f86e30a4cb181d292e8a7b6389058e0c4a8ecf2cf0628` — External documentation describing the checkoutAddPromoCode mutation and webhooks.
- **1/2** `e98d0d6969285968fee458166a53190ee86859081ecab80061fda82817705b07` — External documentation illustrating the GraphQL query syntax for checkoutAddPromoCode.
- **2/2** `pr-diff:7cad3022154da2647ff11d86f8b051ae6161b47f60de78a5e45906745b9e0925` — Primary diff implementing promo code addition, removal, error display, and badge replacement in OrderSummary.
- **2/2** `pr-diff:262aa2faa4b6c8c817b48d03473a42b40ed2953012aae1b4f1e085e78e3b0c6b` — Primary diff wiring the network-only checkout refetch callback to mobile and desktop summary instances.
- **1/2** `browser:c3b84d117442272e30c6` — Browser crawl of storefront home page showing navigation context leading into cart and checkout.
- **1/2** `browser:5edec0e3223e7922bbbd` — Browser crawl of the cart page verifying progression towards checkout.

## Test-scope check

- Report overstates what the browser test proved: **False**
- Evaluator note: The report accurately notes COMPLETED_WITH_GAPS and identifies that the browser test only validated presence of the discount code input, Apply button, and checkout URL without exercising backend mutations or state updates.
- The labels are draft and the judge is an LLM diagnostic; review before treating scores as ground truth.

## How to reproduce

Run `uv run python evaluation/agent_quality/run_quality_eval.py --run-id live-e2e-pr1-scope-gap-20261005` from the `agents` folder.
Machine-readable result: `evaluation/agent_quality/snapshots/2026-10-05-saleor-pr-1/evaluation.json`

Label basis: Expected impacts were labeled from the read-only GitHub PR #1 diff at head 1622163cffab9d5e1f3cb32647f3c7dea73d5764. The browser test scope was read from config/default/behavior.json. Review these draft labels before using scores as a release gate.
