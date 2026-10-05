# Saleor ingestion run — 2 October 2026

## Current code/UI graph refresh — 4 October 2026

The code/UI-only refresh completed for baseline revision `1b5d6545d34fda38c1fb24712ed4bf3dffc684b8`.
It refreshed 268 code files, 596 import relationships, four browser observations and four
evidence-backed route mappings in Neo4j. It made no LLM, embedding or Qdrant calls.

The TypeScript graph now resolves `@/...` aliases and dynamic imports, linking the changed checkout
components to `src/app/checkout/page.tsx`. The observed checkout page is still in its `Loading...`
state, so this confirms the checkout **route** only; it does not confirm the voucher controls or
their behavior. The configured read-only browser checks for the product list and empty cart passed.
Voucher apply/remove behavior still needs a usable seeded checkout session.

The latest refresh details and full hashes are in
[`code_ui_refresh.json`](../runs/saleor-storefront/6e55bd8b924c0341e21a85d4666ca510838ed4e5/code_ui_refresh.json)
and the complete snapshot is in
[`confirmed_mappings.json`](../runs/saleor-storefront/6e55bd8b924c0341e21a85d4666ca510838ed4e5/code_ui_refreshes/ab66baa2b6984e93b9be758464446960/confirmed_mappings.json)
and [`code_graph.json`](../runs/saleor-storefront/6e55bd8b924c0341e21a85d4666ca510838ed4e5/code_ui_refreshes/ab66baa2b6984e93b9be758464446960/code_graph.json).
To repeat these stages without model calls, run `uv run ingest configs/saleor.json --code-ui-only`.

The 2 October section below records the earlier document-ingestion run and its status at that time.

Status: **COMPLETE**, with requirement candidates pending semantic and scope review.

- Corpus run: `44dd49dc16a84d2e82153ebd7d65c6ee`
- Extraction run: `27bb24849c9242c1b34fcb567e27e1f5`
- Model: `gemini-3.5-flash-lite`, configured explicitly through `provider: gemini`.
- Finished at 08:09 UTC (13:39 IST); independent read-back verified at 08:10 UTC.

| Stage | Verified result |
| --- | --- |
| Collection, parsing and chunking | 9 documents, 121 chunks; no source errors; raw and normalized hashes verified |
| Qdrant | All 121 persisted vectors reopened and verified against source payloads and cached vectors; 768 dimensions |
| Embedding reuse | 121 cache hits; zero new embedding requests in this run |
| Extraction | All 121 chunks processed; 65 returned an explicit no-requirement reason; no pipeline errors |
| Candidates | 96 after consolidation: 62 `GROUNDED_CANDIDATE`, 34 `REJECTED` |
| Neo4j | Exact run contains 9 document references, 121 chunk references, 96 candidates and 96 citations |
| Evidence | 62 citations pass quote-presence validation; all citations have `semantic_verified=false` |
| Coverage | All 96 assessments remain `NOT_EVALUATED` |
| Tests | 106 offline tests passed, 91.30% combined coverage; fully live Flash-Lite → embeddings → Qdrant/Neo4j test passed |

[Machine-readable verification](../artifacts/ingestion/saleor-run-44dd49dc.json) ·
[Active run](../artifacts/ingestion/active-run.json) · [Testing details](testing.md)

## Inputs and configuration

Edit [configs/ingestion/saleor/project.json](../configs/ingestion/saleor/project.json): `sources` selects
files/URLs and their loaders/parsers; `extractor` and `embedding_provider` select the
models. Credentials remain in `.env`. This run freezes its configuration in the
corpus manifest; changing the project JSON affects future collections.

| Source | Chunks | Authority |
| --- | --- | --- |
| `storefront-readme` | 26 | `frontend_spec` |
| `products` | 4 | `backend_contract` |
| `checkout` | 5 | `backend_contract` |
| `checkout-lifecycle` | 9 | `backend_contract` |
| `shipping-address` | 18 | `backend_contract` |
| `vouchers` | 29 | `backend_contract` |
| `apply-vouchers` | 13 | `backend_contract` |
| `add-promo-api` | 8 | `api_contract` |
| `remove-promo-api` | 9 | `api_contract` |

Only these configured inputs were ingested. The README is pinned to the upstream
baseline revision. Online backend/API documentation is unpinned, so its applicability
to the historical storefront needs review. No PR diff, patched deployment, curated
reference requirements, repository instructions, or manual test results were used
as ingestion input. Code and browser exploration remain later stages.

## Evidence review remains necessary

The 34 rejected candidates are retained in the run's `review.json` and marked in
Neo4j. Sampled quote failures include missing Markdown emphasis and link syntax.
The strict quote check was preserved. Rejected candidates must be excluded from
supported evidence during retrieval unless they are subsequently reviewed and repaired.

Matching a quote does not establish that it entails the whole requirement, fits
the project scope, or is implemented in the UI. A sampled rejected payment-finalization
candidate also falls outside the configured before-payment scope. No exhaustive
semantic or scope evaluation was performed. Backend/API candidates cannot establish
frontend behavior independently.

## Local artifacts

The run directory is `runs/saleor-storefront/44dd49dc16a84d2e82153ebd7d65c6ee`:

- `corpus.json`: frozen configuration, document provenance and all chunks.
- `extraction.json`: the complete candidate set, validation flags and source citations.
- `review.json`: the 34 rejected candidates requiring review.
- `vector-index.json`: complete indexing manifest and embedding profile.
- `sources/`: original content and normalized text, with hashes in the corpus.
- `extraction-cache/` and `embedding-cache/`: reusable results, keyed by content/configuration.
- `events.log`: safe progress/count events for this run.

These local artifacts and Qdrant data are gitignored. This report and its small
verification JSON contain no credentials. Historical failed runs remain available
for audit; retrieval must use this exact run ID and embedding profile.

## Provider recovery

Earlier runs using Flash 3.7/3.8 returned 503/504 errors; the Interactions attempt
also encountered a 429 after six chunks. Its checkpoint is preserved in the
[historical run report](../artifacts/ingestion/saleor-run-9ea28d00.json). An optional,
tested Interactions adapter remains available, but it is not the selected default.

Flash-Lite passed six representative extraction checks before being selected in JSON.
This complete run needed no application-level recovery attempts. Calls were paced
at five per minute, with a 45-second request timeout and one SDK retry allowed.
Collection, indexing, extraction and graph publication took approximately 24 minutes.
No paid provider fallback or billing change was made. Provider availability is still
external to the library.

Retrieval, browser coverage, code dependencies and PR impact analysis were not run
as part of this document ingestion pass.
