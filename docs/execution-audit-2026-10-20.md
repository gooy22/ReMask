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
