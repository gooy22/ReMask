# Business-owned Page creation over HTTP/2

Job `a7894d3e04c84454a2ae6425a854cbbb` retained both existing BM/RK pairs and
attempted to create the missing second Page. Its legacy cache-miss resolver
requested `www.facebook.com/pages/creation/`, which returned HTTP/2 400. The
same session could read Business Suite (200). No Page CREATE was sent.

Prepare now uses the observed Business Suite Page creation flow once an exact
Business exists. Each Business receives its own Page; the first Page and its
existing owner/rights remain reserved. New private profiles defer their first
Page until BM/RK are available. Standalone legacy Page operations remain separate.

## Pinned contracts

| Operation | doc_id | Variables |
| --- | --- | --- |
| Page inventory | 38189452834034346 | `id`, `businessID`, PAGE asset filter, explicit cursor/count, unfiltered search |
| Disclosure precheck | 9501347186640687 | `businessID` |
| Exact category search | 9327506520688752 | `params.search_string` |
| Page CREATE | 37911097395202007 | `input.business_id`, `categories`, `creation_source`, `bio`, `name`, `casd_bl_disclosure_acceptance_data`, `qpl_join_id` |

All four use `https://business.facebook.com/api/graphql/` through the current
profile's proxy/cookies and the existing strict HTTP/2 Business transport. No
runtime JS discovery, Page-creation WWW navigation, browser, OAuth or Graph API
is used for this bundle Page action. Category IDs come from the exact current
Meta category-search response, never a guessed constant.

The mutation sender is `BizKitSettingsCreatePageModal.react` with
`BizKitSettingsCreateAdditionalProfilePlusMutation`. The response's
`additional_profile.id` identifies a profile. Only
`additional_profile.delegate_page.id` identifies the Page for this bundle.
The SDK states that the Page is created and added to the selected portfolio.

## Durable action and proof

1. Match the cookie actor, retained actor, exact Business and Page name.
2. Read a complete, paginated exact-Business Page inventory before CREATE.
3. Reuse a unique eligible existing Page after independent ownership proof.
4. Check current disclosure requirements and resolve one exact category ID.
5. Persist submit intent and the original inventory baseline before POST.
6. Retain returned Page/profile IDs before verifying the canonical Page's exact
   name and `ownerBusiness.id` through the pinned PAGE query.
7. Commit the per-Business Page binding. Run the existing full PAGE_ACCESS
   state machine to verify ownership and assign/verify Page and RK operator rights.

A lost response is recovered through the same Business inventory and PAGE
ownership query. Incomplete reads, wrong owners, wrong canonical IDs and multiple
new same-name Pages do not authorize another CREATE. A retry uses the original
submit baseline; newly discovered Page IDs cannot retroactively become that
baseline. Explicit Meta disclosure requirements are retained as manual steps;
the code does not invent disclosure acceptance payloads.

## Contract evidence and maintenance

The credential-free public SDK snapshot is
`python_backend/tests/fixtures/meta_business_page_observed_20261008.js`.
The pinned manifest records source SHA-256 and identifies live response shapes
as unverified until an actual Meta run confirms them. SDK inspection is not live
success evidence.

Compile a review candidate separately:

```sh
python -m app.contract_maintenance.update_business_pages \
  --source-file tests/fixtures/meta_business_page_observed_20261008.js \
  --output /tmp/business-page-contract-candidate.json
```

The compiler checks persisted IDs, query/mutation kinds, all top-level arguments,
the observed nested CREATE input and category-search sender schemas. It never
updates production manifests during a job.
