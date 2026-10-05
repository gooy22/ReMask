# Automatic Page and created-BM provisioning — 2026-10-05

## Required behavior

One PrgssTeam Page per authenticated Facebook user (`c_user`), reused by duplicate local profiles. Prepare advertising access for every created Business Portfolio and its ad accounts, without requiring a main/owner BM. New RK creation includes Page access preparation. Bulk selection must remain one job and require no per-profile Page choice. Existing-target recovery must reuse confirmed assets and submitted access requests.

After access preparation, prove the Page is selectable in the exact RK's ad form. Bulk card binding follows this flow. Phone readiness must come from account-scoped live evidence; UNKNOWN is not permission to launch.

## Reconciled work and live evidence

| Area | Evidence / current state |
| --- | --- |
| Bulk interface | Existing automatic profile/BM/RK preparation; Node test covers 100 profiles / 200 BM targets in one FP job, excluding personal accounts and duplicate targets. |
| Durable creation | Existing confirmed CREATE records, resumable job state, and common-Page registry. Alias recovery now shares the same uncertain Page-create checkpoint by Facebook UID and retains its original local profile scope. |
| Queue limits | Actual default browser pool is one. Queue timeout waves now use that same default instead of assuming two browsers. |
| Created BM identity | Actor-wide inventory on the failing account returned only the personal scope. Exact durable confirmed CREATE now resolves the created BM ID/name; current access is verified separately. Live retry proved this resolution worked. |
| Existing partner access | Native Page access screen shows both created BMs as partners with Insights and Ads. Automated row extraction failed despite the visible partner control. Added the exact native partner-menu anchor, deeper scoped ancestor reading, and diagnostic traces. Live validation of this change is pending. |
| Operator assignment | Existing code clicks Assign/Save but does not independently prove the saved operator Ads task. This remains an incomplete success criterion. |
| Ad identity form | Current inspector cannot report `identity_form_verified=true`. The read-only live probe stopped at Loading your ad account under the previous early memory guard. Actual Page selector remains unverified. |
| Memory | Live sample was 861.6 MB / 953.7 MB. It proves the old 85% guard stopped the inspection, not an actual OOM. Added working-set/cache accounting, a separate hard limit, and the same 256 MB V8 budget as the lightweight access flow. Must measure live after deployment. |
| Cards | Existing encrypted vault, masked selection, no persisted CVV, exact-target serial binding, uncertain-result reconciliation. UI regression passed. CI vault test used short substring matches inside random ciphertext; changed it to JSON-field and decrypted-payload checks. Actual Meta card binding remains unverified. |
| Phone | UNKNOWN. No live account-scoped evidence yet establishes whether launch can proceed without phone verification. |

## Verification performed

Production identity fix `dd838c8964b457979b6e6d8acc20a06b460ed1ce`: owner-access tests, Docker diagnostic, and recovery-image build succeeded. Full regression ran 777 backend tests successfully and passed bulk/profile/payment UI checks; failed at the random-ciphertext substring assertion described above.

Current local access/recovery/inspection/queue suite: 81 tests passed. Pure Node DOM fixture proved the deep exact partner-menu anchor and rejected Ads leaking from a neighboring partner row. No paid campaign was published and no card charge was performed.

## Remaining sequence

1. Deploy partner-row, alias-recovery, queue and memory fixes; retry only the existing failed Page-access step.
2. Prove existing target-BM Ads partner access and operator Ads access without recreating assets or resending recorded requests.
3. Inspect the exact created-BM RK ad identity selector and add a truthful form verification path.
4. Verify bulk cards through native billing preparation and account-scoped result reconciliation.
5. Record the actual phone requirement from the same RK. Stop treating missing evidence as readiness.

This document records observed progress, not an end-to-end completion claim.
