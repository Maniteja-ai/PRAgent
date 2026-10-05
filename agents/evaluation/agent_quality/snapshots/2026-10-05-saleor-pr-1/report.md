# Pull request impact report: live-e2e-pr1-scope-gap-20261005

**Status:** COMPLETED_WITH_GAPS

## Summary

This pull request updates the checkout order summary flow by connecting promo code redemption and removal directly to Saleor GraphQL mutations instead of relying on a hardcoded local state, and refetches checkout data on changes across mobile and desktop summary views.

## Findings

These findings are evidence-based impact predictions. They do not by themselves prove that a runtime failure occurred or that a user flow was tested.
### Checkout Promo Code Submission and Error Handling

Shoppers attempting to redeem promo codes in the checkout order summary now trigger the checkoutAddPromoCode GraphQL mutation rather than toggling hardcoded mock state. The UI displays API and checkout validation error messages and disables input controls while the mutation is in flight.

Evidence: pr-diff:7cad3022154da2647ff11d86f8b051ae6161b47f60de78a5e45906745b9e0925, c598135bac2d5a79a49b8956bd7033e372485535b5bb492cc4e56a78ac19d3bd, neo4j:89c4b5becd48c97277de2b4615bd2b3278ebb19dbfa509013533ff322e889f92

### Applied Voucher Display and Removal in Order Summary

When an active voucher exists on the checkout (checkout.voucherCode), the discount input form is replaced by a badge displaying the applied code and discount name alongside a removal button that calls the checkoutRemovePromoCode mutation.

Evidence: pr-diff:7cad3022154da2647ff11d86f8b051ae6161b47f60de78a5e45906745b9e0925, c598135bac2d5a79a49b8956bd7033e372485535b5bb492cc4e56a78ac19d3bd, neo4j:89c4b5becd48c97277de2b4615bd2b3278ebb19dbfa509013533ff322e889f92

### Live Checkout Summary Synchronization on Add or Remove

SaleorCheckout now passes an onCheckoutChange callback to OrderSummary on both desktop and mobile viewports, invoking refetch with network-only policy upon successful promo addition or removal to refresh totals, taxes, and shipping costs.

Evidence: pr-diff:262aa2faa4b6c8c817b48d03473a42b40ed2953012aae1b4f1e085e78e3b0c6b, pr-diff:7cad3022154da2647ff11d86f8b051ae6161b47f60de78a5e45906745b9e0925, neo4j:d02e46ac4aff541b8d5f4e8b1a30379558a33db138c0af47d5a0eb1079649026

## Behavior checks
- **PASS** `checkout-discount-controls-render` — All 3 configured assertions passed.
  - Verified checks:
    - input[placeholder='Discount code']:visible is visible
    - button:has-text('Apply'):visible is visible
    - URL contains '/checkout?checkout='

A PASS covers only the listed assertions; it does not mean the complete user flow was verified.

## Coverage gaps
- Behavior scenario 'checkout-discount-controls-render' checks page/control presence or URL only; business behavior outcomes are unverified.
