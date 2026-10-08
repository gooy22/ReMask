# Static cookie-session Settings chain

Production BUSINESS, AD_ACCOUNT and private PAGE_ACCESS use a deployed manifest
at `python_backend/app/contracts/meta_settings_20261008.json`. No JS/CDN module
fetch, doc_id cache expiry, candidate iteration, Chromium lease or browser-native
mutation belongs to this chain. The profile HTTP bootstrap still obtains current
cookies/actor/CSRF/request context; those values are never stored in the manifest.
Business-origin HTTPX transport enables HTTP/2 and disables HTTP/1 and retries.
The proxy CONNECT protocol is independent from the origin HTTP/2 connection.

| Operation | Pinned doc_id | Variables |
| --- | --- | --- |
| BM scopes read | 28183223691347905 | Observed all-first-level-scopes preload variables and explicit provider flag |
| Exact BM / permission config | 38731886369758684 | businessID, shouldDefer |
| Exact BM RK read | 26033539376343649 | businessID, assetTypes, count, filters, ordering and discovery flag |
| CREATE BM | 28057338880523368 | input, as in the existing production capture |
| CREATE RK | 37914283651496237 | businessID, adAccountName, currency, timezoneID (string), endAdvertiserID, qplJoinID |
| Page ownership read | 27128708340114788 | pageID, businessID, isMMAPageClaim, isMMAPageTransfer |
| Asset/user rights read | 28784182837934802 | assetID, businessID, userID, surface |
| Claim existing Page | 27720923774230959 | Nine observed flat Settings variables, KEEP direct users, no ownership transfer |
| Assign full asset rights | 10073595179327842 | businessID, userID, assetID, taskIDs, assetTypes |

BM enumeration sends the observed selector preload instead of treating redirected
Home HTML as inventory. Only an error-free typed Business scope collection with
explicit final-page / explicit exact-total proof permits absence. A name match in
a partial list does not prove uniqueness. An expected BM is independently read
through the exact CONFIG query. RK reads retain canonical IDs separately from UI
asset IDs; a partial or foreign-BM response never proves absence. Exact pending
IDs can still be reconciled when the full collection remains partial.

Every mutation keeps the existing durable submit intent and independently reads
actual state before COMMIT. Lost responses never switch transport or contracts,
and new jobs verify retained intent before any new mutation. Last confirmed
Workspace state and the existing repair-before-new-slot planner are preserved.

## Permission implication correction

The live job `f1f83ec41c6543bca4dedf5f0951022b` / profile 15 failed after CONFIG and
Page reads because a valid implied Page task was absent from the variant's visible
controls. Public Meta SDK `PermissionTasksImplicationHelpers` adds declared implied
IDs without requiring a visible control for each dependency. The implementation
now follows that rule; malformed IDs remain rejected and independently assigned
full rights still must be confirmed for the exact asset and Business user.

## Standalone permission response correction

The next live job `d4bdd7d6dbe243838677db5149fe5fb4` confirmed Page ownership
and submitted Page assignment over HTTP/2, but stopped during rights verification.
The observed response keeps `asset`, `user`, `current_business` and
`assigned_permission_task_ids` as siblings inside
`data.business_object_rendered_in_ui.user_assigned_permissions`. The parser now
binds that one record to the exact asset, scoped Business user and BM; available
task definitions, viewer permissions and matching unrelated branches are never
substitutes. Retained assignment intent is reconciled before another mutation.
Tests cover this observed structure with synthetic values, foreign relations,
malformed/partial task lists and cross-job verification without duplicate POST.
Live completion after this parser correction is still unconfirmed.

## Maintenance is separate from action execution

`python -m app.contract_maintenance.update_settings --sources-dir tests/fixtures
--output /tmp/new-contract-candidate.json` compiles observed snapshots into a new
review candidate. It validates artifact/sender schemas, rejects incompatible
variables, never overwrites the production manifest and never sends HTTP or asset
mutations. New query/response shapes need source evidence plus regression tests
before a reviewed manifest deployment. Dynamic discovery utilities remain for
legacy/maintenance callers; production Prepare does not call them.

## Evidence limits

RK creation/read and the Page config/ownership contracts come from observed
current modules and earlier live RK success. CREATE BM reuses the existing
2026-09-24 capture. The all-first-level BM enumeration doc_id and sender variables
are observed, but its live response has not yet been obtained: the manifest marks
that explicitly. The rights response parser also still needs live end-to-end
confirmation. Fixture success does not certify a successful live complete bundle.
Response diagnostics record bounded paths/types/counts, without tokens or scalar
response values, so an unmatched live response can be repaired from evidence.

The existing profile-level singleton Page cannot be owned by two portfolios.
This change does not certify full multi-BM ownership using one shared Page or
change the Page topology without a separate product decision.
