# Continuation audit and inventory correction — 2026-10-20

## Verified baseline

- Repository main: `25e471500fb3502326bcec0695a49aa687d78ed4`.
- PRs 204–210 are merged; PR 199 remains open. Its mere presence is not a pending deployment requirement: relevant fast-Sync changes were integrated in 204.
- Production project: `bb08bc86-ba4d-4845-b9b3-a0a669df712c`; service: `c279cdd8-e550-4d19-9ff0-4d90e7886853`; environment: `d936e3b7-ab2f-4093-86c0-9b9144e7e28d`.
- Observed configured image: `ghcr.io/gooy22/remask@sha256:0dc39b58ccbb28acaccda97ff26cfc548884d9bf6d2735f35e52a82e90a00ce9`.
- Recovery-image workflow 39392169767, job 118526057555, independently associates that digest with the above commit. Regression workflow 39392169728 and Docker Diagnostic 39392169720 succeeded.
- Deployment `09cc8a31-ac8a-480d-9765-96943c67a5df` was observed sleeping before opening ReMask. The subsequently loaded ReMask worker reports READY, revision `25e471500fb3`, 20 profiles, mounted volume and an empty queue.
- Volume `0e57d2c6-8ac8-46d9-a937-4d20a01ca4df` remains attached. Railway's Backups page reports no backups and does not offer new backups on the current plan. This correction introduces no persistent-state format migration. The previous immutable image remains the code rollback target; that is not a data backup.
- Both connected Railway OAuth accounts lack viewer access to this exact project. Platform observations used the previously authorized Railway browser session, not another similarly named project.

## Production boundary

ReMask's initial local Workspace load displayed 19 saved BM and 18 saved RK across 20 profiles. Saved counts are not live authorization evidence.

One read-only Sync was initiated through ReMask for profile 35, current actor `61595268505592`, preserving its configured proxy. Meta returned `CHECKPOINT_REQUIRED`; the saved 1 BM / 1 RK remained visible. No additional Meta reads or creation/publication attempts were performed for this actor. Resume live acceptance only after legitimate session restoration is independently established. No account switching is used to work around this restriction.

A separate retained production log at 2026-10-20 07:15:48 UTC records a received advertising read rejection: code 10, HTTP 200, OAuthException, permission/business/account terms. This establishes a permission failure, not a transport outage or successful Page authorization. It does not establish the current state of another account.

## Source paths checked

The audited chain is Workspace `syncSelection` → authenticated `metaHierarchy.php` overlay → `profile_live_inventory` → `ProfileSession` / reviewed static inventory → completeness decision → PHP reconciliation → Workspace snapshot application. Launch investigation also traced direct/bulk target binding, optional creative-map serialization, private account alias routing, promotable Page reads, payment preflight and the existing draft/publication engine. PRs 205–210 are preserved, including selectable cached Pages that remain unverified for publication.

## Reproduced and corrected defects

1. A partial BM selector prefix, followed by complete RK reads for its visible BMs, could mark full-profile Sync ready. HTML-contained BM rows also set `discovery_complete` merely because at least one BM was present. Two regression cases failed before the correction. Full interactive Sync now requires explicit complete BM enumeration and complete scoped RK reads; exact selected-BM checks retain their limited scope. Positive identities remain in the diagnostic result and an incomplete result cannot replace the last confirmed Workspace inventory.
2. HTML discovery cannot establish complete BM enumeration for the interactive contract. Fast Sync no longer spends time on these non-authoritative discovery probes after an incomplete selector result. It returns `PRIVATE_BM_INVENTORY_INCOMPLETE`; the explicit legacy rollback flag retains the older discovery path.
3. PHP workers lacked a per-profile live-Sync lease. The route now takes a private nonblocking file lock before worker I/O and reconciliation. Another request for that profile returns `SYNC_IN_PROGRESS` without a Meta read; different profiles remain independent. OS errors fail closed, private permissions stay 0600 and process exit releases the lease. A two-process PHP regression executes the actual handler's busy branch.
4. Lost-response reconciliation had no timeout on its local status reads. Each status read is now bounded by 3 seconds and the polling window by 16 seconds. A stalled status read stops further polling. This never resubmits the Meta operation. A deterministic JavaScript regression verifies one original Sync, one stalled local result read, preserved inventory and released UI busy state.

## Timing contract

The worker returns and logs non-secret `timings_ms` on both complete and incomplete results: profile context, durable-state reads, HTTP-session setup, inventory, optional Page enrichment and total. Nested Meta timings distinguish BM discovery and each scoped RK inventory. Unexecuted enrichment phases are absent, not reported as successful.

The PHP response adds `sync_timings_ms` for worker-bridge wall time, PHP reconciliation and total time through response assembly, plus the worker timings. JSON serialization, browser rendering and network transit are not included in those server measurements. No production latency claim is made from offline test timing.

## Remaining acceptance work

- Legitimate restoration and independent live readiness for the selected test actor; no successful publication is claimed.
- Page advertising permission and complete payment readiness must remain publication gates. A cached selectable Page does not satisfy either gate.
- Independent review outcomes are now collected for every locally valid selected target, including blocked and payment-selection states. Auth gates stop further reads for that profile; a ReMask 401/403 stops remaining requests globally. A verified quota refusal (HTTP 429, a typed rate-limit code, or the existing sanitized Meta quota diagnostic) stops all remaining reads in the batch with `RATE_LIMITED`, without switching accounts or retrying. The UI shows the last per-target result with exact scope. The existing batch admission policy remains `all_required`: a blocked target does not silently disappear from the requested batch, no mixed batch is enqueued and the existing durable idempotency request is unchanged. Partial admission requires a separate explicit policy and per-target replay design before enabling it.
- Full background contract-maintenance scheduling, standalone serialization/rendering measurements, production replay/restart acceptance and controlled 1/3/5/10/30-target validation remain separate milestones.
- This report covers the traced execution paths, not a claim that every captured Facebook module implements an application feature.

## Release evidence

The Sync correction was integrated in PR 211: source `d30f5ccad3db80c6ba997ec92c094362d3d160b7`, merge `df41295fdff4b95afc505ff6e63febbbe785a5e4`. The final local backend suite ran 1,912 cases successfully with 83 approved development-renderer skips. The main CI regression, browser, payment-guard and immutable image jobs passed.

Production deployment `7a0ebb31-f55e-4bbb-8082-7608574e36cc` reached Active / Deployment successful with image `ghcr.io/gooy22/remask@sha256:f84bdf55c01906c7c80af6efd8fb88ef9cb085a8a71bfb52c15c42bdffca976f`. ReMask subsequently reported READY, revision `df41295fdff4`, 20 profiles and mounted volume; profile 35 still displayed its saved 1 BM / 1 RK. `REMASK_DEPLOY_REV` was updated with the matching merge SHA. Fast-Sync overrides were absent, so installed defaults apply. Startup Meta canary/audit profile settings were empty and BM/E2E startup checks were disabled; deployment did not initiate another attempt against the gated actor.

The separate batch-review correction has deterministic 1/3/5/10/30-target tests. These cover preserved ordering and exact Page/Business scope, account-local denials, per-profile auth stop, global ReMask auth stop, waiting for payment selection, refusal to admit a mixed batch, and one 30-target PAUSED job with distinct existing idempotency keys. They are fixture results, not live scale or publication evidence. The read-only review bridge releases the authenticated PHP session after validation so one slow review cannot block the operator's other local requests. No authentication or CSRF checks are removed. Typed Python readiness codes now survive the PHP boundary instead of being hidden inside a generic error string. A real local HTTP/PHP integration test confirms exact target forwarding, the typed error response and that the session file is unlocked while PHP waits for the worker.

Source integration, CI, deployment and live acceptance of the batch-review correction must be recorded separately. Actual advertising publication remains unverified.

## Launch execution continuation

Current main `6b7dfc139c604bd74309cb0eb774a647830c8e11` has successful regression, browser, payment-guard, Docker and image workflows. Production was independently observed READY at revision `6b7dfc139c60`, 20 profiles and mounted volume before the new authorized import. Its active deployment is `b38d1955-6750-4000-b004-7efaf7567e00`.

A reproducible publication-recovery defect was corrected in the implementation: after a saved PUBLISH receipt, retries previously required fresh draft/fragment rows before reading published objects. Draft rows are no longer a recovery dependency. Recovery validates every saved request fingerprint, actor/profile/Business/RK binding and confirmed hierarchy, then reads the exact publication status and Campaign/AdSet/Ad/Creative objects independently. `saved_only` cannot claim or dispatch a new POST if any receipt is missing. Unknown replies remain non-replayable; cached confirmation alone never marks publication successful. Fresh creation retains its live Page advertising-permission and payment-readiness gates. No data migration is introduced.

The changed implementation necessarily changes the source digest. One audited predecessor is explicitly supported: main `6b7dfc139c604bd74309cb0eb774a647830c8e11`, source/catalog digest `e38dc7d32ba2ccd73d18e0b31b477be7e1d4f94624d1b1a1817efd692307a2c2`. Its accepted publication may resume only with the original actor, scope, complete confirmed hierarchy, creative binding and PAUSED request. The original digest and operation fingerprints remain exact; no journal is rewritten to a new digest. The HTTP client becomes read-only and recovery never enters delivery mutation. Unknown/mixed predecessor digests, missing receipts, actor changes, requested activation and freshly read non-PAUSED objects fail closed. This exception cannot resume draft creation, media upload, publication submission or activation.

Two regression tests failed before the fix. Added coverage includes interruption after publication, reopened SQLite state, consumed draft rows, changed input, missing confirmation, foreign published IDs, and a saved-only request with no receipt. The existing Launch entrypoint test verifies recovery reads every published object and sends no additional media upload or creation/publication POST. These are deterministic fixture results, not live Meta publication evidence.

The operator explicitly supplied a new test account in `purchase_items409161.txt`. ReMask imported it as profile 38 (actor `61594769837340`) using the saved profile-35 proxy, the most recently used known route. Password/2FA fields were discarded by the import path. No secret is included in this report. One UI Sync reported partial Fan Page inventory and no confirmed BM/RK; one UI Prepare job `b8718520e8724351832c163cd999d047` was submitted for one FP/BM/RK. Retained production logs establish terminal SUCCESS at 2026-10-20 10:24:46 UTC, with confirmed BM/RK/FP creation and effective Page/account assignments. This existing kit must be reused. Advertising Page eligibility, payment readiness, actual Meta draft and publication acceptance remain independent milestones. The earlier profile-35 checkpoint remains untouched.

PR 214 was merged as `7b6a98501f4d2f18ad4ad0f6677120c822a3564e` after every PR job passed. All main regression/browser/payment-guard, Docker and recovery-image jobs then passed. Verified image digest `sha256:ec1efb78e54793668653795de8b5f9eb71beae05415b4165784b28785a4071ce` was deployed to the existing production service. Deployment `d4e5c201-e7d7-43a3-8515-46de34ad405a` reached terminal SUCCESS. `/ready.php` and `/health` returned HTTP 200, and startup reported `[profile-storage] ready count=21 encrypted=1 key_encoding=hex`. The volume, credentials key and saved jobs were preserved. Startup logs also enumerate retained legacy provisioning diagnostics; those are not new browser execution or newly submitted operations.

## Actual Meta draft versus publication

The operator clarified that profile 38 is a new account and requested no payment inspection for this task. The existing Launch implementation unnecessarily combined draft creation with publication, placing a payment gate before either operation. A separate explicit DRAFT mode now retains exact account/Business/Page access and creative/asset checks, uploads selected media, and independently reads the real Meta draft and each Campaign/AdSet/Ad fragment. It returns `draft_verified=true`, `published=false`, and no final published-object identities. It does not inspect payment methods, submit PUBLISH, inspect publication status, or enter delivery mutation. Draft object references are explicitly not final published Campaign/AdSet/Ad/Creative proof.

The existing PUBLISH mode keeps its payment and independent final-object verification gates. Mode is part of the durable launch fingerprint, so changing DRAFT to PUBLISH cannot repurpose an existing operation key. The UI defaults to the explicit draft choice and disables delivery actions for draft-only source jobs. Missing/uncertain receipts remain non-replayable. Read-only accepted-publication recovery also recognizes the audited PR-214 predecessor source digest `cc0d9d896c67b95db8bdef3fce77279ba9d9446302f0bd0f1c10a1e62ba92645`, with the same strict restrictions.

Local validation: 148 private-advertising tests pass, including full targeting values, independent draft reads, repeat without duplicate POST/upload, no billing/publication/delivery, wrong mode, changed operation mode and mismatched fragment settings. Node tests cover draft job serialization, stale target overrides, separate mode fingerprints and unavailable delivery actions. Installed browser checks, CI and live production acceptance must be recorded separately.
