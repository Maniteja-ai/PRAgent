# Saleor PR #1199 setup

## Source control

The two pushed branches contain unmodified upstream application code. Baseline is `23bc49ccd22e13e182b30daff562d5e5c9af874c`; patched is `221be2247f5b1a8ef94f007639fe83f53a6384b8`. Their diff contains exactly two checkout files. The original API-reported PR base is preserved in the manifest for provenance.

## Backend

Sandbox endpoint: https://store-vlrmbn6z.saleor.cloud/graphql/

Channel: `default-channel`. A public products query succeeded on 2026-10-02 and returned sample products. The cloud UI reported Saleor 3.23.37. This is a mutable sandbox, so record the actual version during each later evaluation.

Keep the existing sample catalog. Choose a stocked, published product in the default channel. Use a supported destination country, shipping zone, and shipping method. All test orders must remain in this sandbox.

Two reusable test vouchers were created and verified active in the dashboard on 2026-10-02, in Channel-USD (`default-channel`), with no usage cap, single-use setting, minimum purchase, or end date:

| Code | Type | Value | Purpose |
|---|---|---|---|
| TSIGMA10 | Entire order | 10 percent | Valid application/removal |
| TSIGMASHIP | Shipping | 100 percent | Shipping prerequisites and error handling |
| TSIGMA-NOT-A-CODE | Do not create | Not applicable | Invalid input |

Ensure voucher channel availability and dates include the test time. Do not assume a code exists until the dashboard/API confirms it. Fixture completion is tracked in the manifest.

## Storefront setup

The original repository pins Node 20.x. Vercel rejected that runtime as discontinued, so both deployment branches use the same one-line change to `package.json`: Node `>=22 <23`. The original analysis branches remain unchanged. Use Node 22.x and pnpm 10.28.1 for the deployment branches. Install with the lockfile unchanged:

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

The two Vercel projects are `testsigma-saleor-baseline` and `testsigma-saleor-patched`, using `assignment/deploy-baseline-pr-1199` and `assignment/deploy-patched-pr-1199`. Confirm the actual deployed source SHA in build details. Platform compatibility changes are identical on both branches and excluded from the original PR analysis.

The initial build also reproduced `USE_CACHE_TIMEOUT` in cached navigation on account routes. In `src/lib/graphql.ts`, authenticated requests now bypass the shared public-data queue: a request waiting for runtime cookies must not hold a slot needed by prerendered public data. This matches Next.js 16.2.6's `use cache` guidance on avoiding shared promises across cached and runtime contexts. Both deployment branches receive this adjustment; their cross-branch diff still contains only the original two checkout files. Build and runtime results are recorded separately from this diagnosis.

## Validation order

1. Confirm production build success and public URL for each revision.
2. Open the storefront and a product detail page.
3. Select an available variant, add it, and verify cart quantity.
4. Start checkout in a fresh browser session.
5. Apply the configured voucher; record visible state and API response.
6. Remove the voucher, then try the invalid code.
7. Test shipping voucher prerequisites.

Do not complete a real payment. Stop before payment, or use an explicitly configured sandbox payment provider if order completion becomes part of the later scope.

Manual browser testing confirmed the core difference: baseline did not redeem `TSIGMA10`, while the patch applied and removed it through Saleor and displayed invalid-code errors. Shipping prerequisite and successful shipping-voucher checks also passed on the patched storefront. See [validation and evidence](validation.md) for the exact scope and observations. This manual setup validation is separate from the future agent's evaluation.
