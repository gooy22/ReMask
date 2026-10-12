# Private advertising continuation on Railway main 0f29bf5

Base: production commit `0f29bf5112a893d94ee8aa8e37686d44763b9995` (PR #132 image carousel implementation). The prior alternative adapter is preserved locally at commit `60227e8`; this change extends the deployed `private_ads_*` engine and keeps its durable claims and independent verification.

## Implemented

- Fresh literal `WebApiApplication.init` joins the existing ApiClient setter initializer. Captured application 119211728144504 only; conflicting initialization fails. Ephemeral authorization stays in server memory. Cookie/proxy changes require fresh initialization, including a second check after the durable claim.
- Promotable Page eligibility reads `ad_account_promotable_objects.promotable_page_ids`, as the captured AFCPromotableObjectDataManager does. Account ID and Business relation are independently checked first.
- Installed public-source catalog: 27 routes, source module/script SHA-256, field mappings and literal constants. The resource adapter uses the deployed HTTP/2 client; it adds no separate runtime transport or browser.
- All 17 resource definitions are connected to the existing advertising resource API/cache: Pages, images, videos, used creatives, Instagram accounts/media, pixels, custom audiences, conversion goals, campaigns, AdSets, ads, interests, behaviors, locations, locales and insights. Pagination is bounded and never follows credential-bearing next URLs. The creatives list contains creatives referenced by account ads; unused creatives are not claimed as a complete inventory.
- Serial video start/transfer/finish use source-defined FormData, matching waterfall header, checked offsets, bounded chunks, managed-file checksum and durable phase receipts. Finish is followed by account membership, encoding and thumbnail reads. Encoding pending resumes READs rather than uploading again.
- Single video, mixed image/video carousels and eligible existing Instagram posts feed the same three-level draft publisher and full independent four-object verification. Instagram media retains its eligible actor binding. Selected pixels, audiences and conversion goals are freshly read before mutation.
- Create remains PAUSED. Desired activation follows verified publication, child first. Standalone delivery uses only a saved verified source hierarchy, exact actor/account/Business/Page, separate idempotency key, owned modify fragments, saved async publication handles and independent final states. Pause proceeds campaign first.
- Publish arguments now include the observed top-level `ignore_errors=false` and `include_fragment_statuses=true` rather than nesting caller options in an unobserved envelope.
- Known completed media receipts can be independently verified without the original local files. Partial uploads still require managed bytes. Unknown POST replies remain non-replayable.
- Jobs exposes verified publication IDs, separate delivery steps and upload-phase acknowledgement without calling a byte acknowledgement a verified asset. Non-chunk steps remain visible when the upload journal has many chunks. UI persists only the source job ID, restores status after reload, retains submission keys when storage fails and tolerates bounded status-read interruptions.

## Other Facebook definitions refreshed

The public-source audit observed 18 of 23 installed operations. Two payment query entries (`READ_OPTIONS`, `READ_VERIFY_OPTIONS`, BillingSelectPaymentMethodPageViewManagerQuery) changed docID from 29195809800004536 to 28265228839826899; argument schema and exact-account response fields were confirmed in the artifact. READ_ACCOUNT gained three captured literal defaults; READ_BM gained ten. Other observed operation IDs remained unchanged.

The audit evidence records the pre-refresh comparison. SAVE_CARD fields supplied by its dynamic input builder are not missing literal defaults and were not replaced with nulls. Five operations were not observed in the loaded public-source corpus: READ_FP, READ_SETUP, CONFIG, RIGHTS and READ_RK. They retain their installed definitions. New country/currency decision queries were not substituted without proof of the required account response mapping.

## Validation deferred

No application test suite, live advertising mutation, payment/card submission, bank authorization, worker canary or deployment was run for this continuation, per the user's proxy/testing constraint. Only Python/JSON/JavaScript syntax and whitespace checks were performed. Added offline cases are for the later validation pass; they have not been run here. The production Railway deployment remains 0f29bf5.

Public sender/artifact evidence establishes schema, not successful live behavior. Fresh document initializer layout, actual resource responses, PAUSED image/carousel/video/Instagram canaries and delivery readback still need verification through the assigned working profile proxy.

Saved plans are bound to the complete adapter/catalog digest. Jobs from the earlier deployed adapter revision are intentionally not silently resumed with changed source code. Their receipts remain intact; reconcile them on their matching revision before introducing an explicit migration. Replacing an unknown-reply job with a new key is not a recovery method.
