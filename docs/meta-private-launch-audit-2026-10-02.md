# ReMask: private Launch catalog and verification audit, 2026-10-02

## Confirmed defects and scope

The saved Workspace RK/Page list was still loaded through legacy Meta Graph preflight/assets endpoints. A stale token could prevent opening already confirmed assets and restart live synchronization unnecessarily. The private worker snapshot is now used for these local catalog reads. This is identity/catalog data, not current Facebook session readiness or advertising permission.

FUNDING previously skipped new jobs when a scope contained funding_source_id and accepted a transport result containing only that ID. It now requires explicit funding_verified=true plus exact requested RK and source IDs. Historical SUCCESS without that proof fails without resubmitting the payment action. A verified completed step in the same job remains idempotent.

Fan Page attachment verifies the Page's relation to the Business, not its advertising access for an RK. The result now separately reports page_business_attached, attachment_scope and ad_account_page_access_verified=false. Existing attachment behavior is preserved.

## Launch behavior and remaining gaps

- Profile existence is checked locally; metaPreflight and the supported metaAssets resources (ad_accounts/pages/funding) do not create a MetaApiClient or contact Facebook.
- Canonical Page IDs deduplicate aliases; profiles and confirmed RK/BM bindings are kept separate. A newly confirmed RK is merged with unknown status/currency rather than invented ACTIVE/USD fields.
- Stale/missing snapshots and unverified session/permissions are explicit. Empty or expired prepay funding cannot report READY.
- Multi-profile catalog reads run serially. BM IDs are retained when building Launch targets.
- Confirmed Page rows are rendered before optional Pixel lookup, and selecting a Page does not report verified advertising readiness.
- Remaining asset resources are unavailable in this private catalog path. This change does not implement private Pixels, audiences, media, campaign/adset/creative/ad submission or card attachment.
- Review, server dry run and new ad job submission still have only a legacy Graph implementation. Their endpoints are stopped before that implementation, with PRIVATE_LAUNCH_VERIFICATION_REQUIRED. This is an explicit temporary limitation, not a completed private launch engine.
- No private card attachment implementation was found. The older payUnsettled endpoint pays outstanding charges and must never be reused as card attachment.

## Live safety and prior evidence

Profile 7; confirmed BM 2478360152656679. Previous single RK attempt job 2f0595ff87fe450d8939ad0338c020d0 failed before Create because Facebook presented CHECKPOINT_REQUIRED. No new RK or card was created by this continuation. No automated checkpoint bypass or repeated Facebook mutation is authorized by these changes. The cloud app UI can be checked independently against saved data without restarting Meta synchronization.

Two canonical FP IDs in the previous confirmed catalog: 1289628847574478 and 1372205759306015. Alias rows are not additional Pages. Origin/time of creation of the second Page is not newly established.

No worker concurrency or Railway capacity is increased; one browser slot is retained. No ten-profile or batch live test is performed.

## Validation

Local: six backend regressions passed, Node private catalog/funding assertions passed, Python compilation and Node syntax passed. Six PHP catalog/installer tests require PHP and will run in CI (php --version required); they are skipped only in the PHP-less local environment. Full existing suite, deployment and one-profile UI verification are pending at the initial commit and will be recorded after completion.

