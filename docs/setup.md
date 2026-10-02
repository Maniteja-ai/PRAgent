# Saleor PR #1199 setup

## Source control

The two pushed branches contain unmodified upstream application code. Baseline is `23bc49ccd22e13e182b30daff562d5e5c9af874c`; patched is `221be2247f5b1a8ef94f007639fe83f53a6384b8`. Their diff contains exactly two checkout files. The original API-reported PR base is preserved in the manifest for provenance.

## Backend

Sandbox endpoint: https://store-vlrmbn6z.saleor.cloud/graphql/

Channel: `default-channel`. A public products query succeeded on 2026-10-02 and returned sample products. The cloud UI reported Saleor 3.23.37. This is a mutable sandbox, so record the actual version during each later evaluation.

Keep the existing sample catalog. Choose a stocked, published product in the default channel. Use a supported destination country, shipping zone, and shipping method. All test orders must remain in this sandbox.

Configure two reusable test vouchers with no usage cap or single-use setting:

| Code | Type | Value | Purpose |
|---|---|---|---|
| TSIGMA10 | Entire order | 10 percent | Valid application/removal |
| TSIGMASHIP | Shipping | 100 percent | Shipping prerequisites and error handling |
| TSIGMA-NOT-A-CODE | Do not create | Not applicable | Invalid input |

Ensure voucher channel availability and dates include the test time. Do not assume a code exists until the dashboard/API confirms it. Fixture completion is tracked in the manifest.

## Storefront setup

Use Node 20.x and pnpm 10.28.1 as required by the pinned repository. Install with the lockfile unchanged:

```sh
pnpm install --frozen-lockfile
```

Set these environment variables separately for each deployment:

```dotenv
NEXT_PUBLIC_SALEOR_API_URL=https://store-vlrmbn6z.saleor.cloud/graphql/
NEXT_PUBLIC_DEFAULT_CHANNEL=default-channel
NEXT_PUBLIC_STOREFRONT_URL=https://YOUR_DEPLOYMENT_DOMAIN
SALEOR_MAX_CONCURRENT_REQUESTS=1
SALEOR_MIN_REQUEST_DELAY_MS=500
NEXT_BUILD_RETRIES=1
```

`pnpm build` runs GraphQL generation before the Next.js build; it therefore needs the actual backend to respond. Test schema compatibility rather than assuming current documentation matches this historical storefront.

Create two Vercel projects, one for each branch. Confirm the actual deployed source SHA in build details. Preserve any platform-required deployment changes on distinct deployment branches and document them; do not silently mix compatibility fixes into the PR diff.

## Validation order

1. Confirm production build success and public URL for each revision.
2. Open the storefront and a product detail page.
3. Select an available variant, add it, and verify cart quantity.
4. Start checkout in a fresh browser session.
5. Apply the configured voucher; record visible state and API response.
6. Remove the voucher, then try the invalid code.
7. Test shipping voucher prerequisites.

Do not complete a real payment. Stop before payment, or use an explicitly configured sandbox payment provider if order completion becomes part of the later scope.

The expected difference based on source review is that the baseline's local promo-code handling does not redeem real vouchers, while the patch adds API-backed apply/remove and error handling. This is a hypothesis to verify in the browser, not a result already observed.

