Profile 38 editor investigation — 2026-10-21
================================================

State: unresolved; no confirmed objective correction or live editor acceptance.

Production baseline: main `2f5444324d8eea3c669ee0bb1ff2e0b383243e68`, Railway deployment
`38ce8c3f-cdc9-4c2a-8f4f-f4cf58840f74`. Workspace reports READY at this revision,
21 profiles, persistent volume present. No new deployment at the time of this record.

Original job `de696c86fa7c4860bc4c1f92e583c053` reported draft storage and independent
fragment reads. Campaign `120249916184370685`, Ad Set `120249916184630685`, Ad
`120249916185000685`; container `28110892345252814`. It submitted OUTCOME_TRAFFIC,
LINK_CLICKS optimization, IMPRESSIONS billing, WEBSITE destination, PAUSED statuses.
The user reports that the real Ad Set editor rejects the objective as unsupported.
Stored-fragment equality is therefore insufficient acceptance evidence.

Job `fc5daf863d2c4ae796ab7107b3728378` was accidentally dispatched during an earlier
browser interaction. Its saved result was read through Workspace's supported
`?job=` view and reports the same draft container. Its individual object/fragment
identities still require reconciliation. Container reuse does not prove absence
of duplicate fragments. Do not replay or delete either attempt.

ODAX hypothesis — unconfirmed
----------------------------

The public `AdsODAXUtils.isODAXTrafficCampaignGroup` helper tests a boolean ODAX
argument and a LINK_CLICKS objective representation. `AdsODAXObjectiveStrings`
labels LINK_CLICKS as Traffic. These are editor/model observations; they do not
establish that the fragment endpoint rejects OUTCOME_TRAFFIC or requires a
particular Campaign field to be added. Campaign objective and Ad Set optimization
must remain distinct.

`AdsDraftFragmentDataManager.getAPIParams` supplies fragment values to the reviewed
`/{draft_id}/addraft_fragments` operation. The missing evidence is the exact
source-to-request field derivation for the selected modern Traffic Campaign,
followed by the saved and freshly read fields as interpreted by its editor.
Until that is established, do not rewrite Campaign.objective or add an ODAX flag.

Source provenance is pinned in `private_ads_objectives_20261021.json`; the ODAX
entries support investigation only. Creation validation uses the existing ReMask
supported subset, not an objective translation or proof of live account eligibility.

Independent browser acceptance
-----------------------------

Opening Ads Manager for the expected native account `1119599940716086` showed
account `1069737579301477` instead. The picker listed one account under the current
operator and no result when searching the expected account. This browser session
has not demonstrated access to profile 38's target account. No terms were accepted,
no account settings were changed, and no ad was submitted from this browser.

Next decisive checks
--------------------

1. Use the read-only saved-draft inspector for both original job keys. It checks
   actor, Business, account, original Launch fingerprint and reviewed source digest
   before opening Meta; then compares fresh fragments with journal bindings.
2. Compare Campaign objective/buying type/budgets, Ad Set optimization/billing/
   destination/targeting, and all parent identities. Inspect native validation
   status/errors separately from settings equality.
3. Inspect the exact existing Ad Set in an authorized profile-38 Meta UI session.
   A different account, a fixture test or a stored draft cannot establish editability.
4. Only after proving the mismatch, implement the corresponding source-backed
   correction. Keep publication/payment/delivery gates intact.

Production reconciliation and fresh reads (2026-10-21)
----------------------------------------------------

Main e3814b8254dbd175cc24dd1a479137d71216dc03 is deployed in existing Railway
service c279cdd8-e550-4d19-9ff0-4d90e7886853. Confirmed image:
`ghcr.io/gooy22/remask@sha256:99247b4c1e34db5af18f2d05f75ac4a4b1fe2c0c2984db580dd2a73f7250df2f`.
Workspace reports worker READY at e3814b8254db, 21 profiles, volume YES.
The service was sleeping before the UI request, then served the existing session.
No duplicate deployment was issued for this baseline.

Both original attempts were inspected through the supported ReMask UI. The
inspection tasks are 6f84d9c040014bb2a7e3f09fc72432cc (original de696c86...) and
d80e82ed123d411386acce2952939fc9 (original fc5daf86...). Both finished SUCCESS,
with mutation_posts=0, editor_verified=false, published=null and recovered sender
f9f8e21841da2cf174bc516941a5581fe1c5b65642783ab84ed2b03ede68c218.
The exact original actor, Business, Settings RK and native RK relation passed.

| Original attempt | Campaign / fragment | Ad Set / fragment | Ad / fragment |
| --- | --- | --- | --- |
| de696c86fa7c4860bc4c1f92e583c053 | 120249916184370685 / 28112675245074524 | 120249916184630685 / 28112675535074495 | 120249916185000685 / 28112675995074449 |
| fc5daf863d2c4ae796ab7107b3728378 | 120249918385670685 / 28114858811522834 | 120249918385700685 / 28114859114856137 | 120249918385740685 / 28114859438189438 |

Both have container 28110892345252814, but the three advertising identities and
three fragment identities differ. Two separate hierarchies therefore exist;
container reuse did not deduplicate the second submission. Neither is deleted
or replayed by this investigation.

Both Campaign fragments report VALIDATED with no active errors. Both Ad Set and
Ad fragments report HAS_ERRORS with active errors. All stored submitted fields
match fresh fragment values, including OUTCOME_TRAFFIC, LINK_CLICKS, IMPRESSIONS,
WEBSITE, PAUSED, daily campaign budget 500, UA, ages 23-33, locale 52, the exact
interest/behavior IDs, Page and uploaded image hash. Parent relations match.
This is fragment evidence, not proof of direct object availability, editability,
publication or absence of other fragments in the container.

Defect identified in the inspector: it discarded active_errors except for a
boolean. The current correction preserves bounded numeric error codes, reviewed
field names and an allowlisted diagnostic vocabulary, without persisting raw
messages, cookies, tokens, URLs or arbitrary response strings. It also attempts
the exact retained Campaign/Ad Set/Ad and referenced Creative READs separately;
a received rejection is explicit unavailable evidence, not proof of nonexistence.
No new operation schemas, creation POSTs, publication, payment or delivery are
introduced. Original journals and selections remain unchanged.

The available Meta UI session redirects the exact requested native account
1119599940716086 to 1082351911351205. Its picker lists one different account and
returns No results for 1119599940716086. No policy was accepted. This establishes
an independent-editor session limitation, not a restriction on profile 38's
working private HTTP session. Editor acceptance is still unverified.

Public ODAX source scripts were downloaded for offline review; their script hashes
match the existing pinned 3c7de32f... and b2ca67cf... evidence. This does not yet
prove the sender-side objective transformation, so no objective/ODAX mutation
has been made.
## Deployed diagnostic follow-up, 2026-10-21

PR #220 merged as `1821a83955ffe93294abfd5082aeaa3b58313eda`.
Docker Diagnostic, every Meta regression job, and recovery image build passed
for that merge. The recovery build confirmed immutable image
`ghcr.io/gooy22/remask@sha256:28be7b4732cc31406fb4a220a26af7554a147708d5b41618a35e53a66b864404`.
Railway deployment `611243e0-587a-48a7-838c-f990c92de9ab` reached terminal SUCCESS.
The exact worker revision was displayed in the authenticated production UI;
`/ready.php` and `/health` each returned HTTP 200 and `ok: true`.
Startup reported `[profile-storage] ready count=21 encrypted=1 key_encoding=hex`.
The existing volume and credentials were preserved.

Two new read-only inspections were run through the supported production UI:

| Original job | New inspection job | Actual fragment validation |
| --- | --- | --- |
| `de696c86fa7c4860bc4c1f92e583c053` | `dd3ef32c60194d13b3701bdf8b822bac` | Campaign VALIDATED; Ad Set and Ad HAS_ERRORS |
| `fc5daf863d2c4ae796ab7107b3728378` | `a18d4a545c9a488cb6d8aacffc98737b` | Campaign VALIDATED; Ad Set and Ad HAS_ERRORS |

Both Ad Set and Ad diagnostics contain Meta `error_code=1815857` and reviewed
field name `bid_amount`, with the terms `field` and `required`. Raw Meta
messages are deliberately not retained because they may echo credentials.
The saved configuration still selects LOWEST_COST_WITHOUT_CAP and a Campaign
daily budget of 500; no arbitrary bid amount or capped strategy was inserted.
This error is separate evidence from the unsupported-objective editor message.
Its originating bidding/default/inheritance defect is not yet established.

For BOTH attempts, independent exact-object GETs returned:

- Campaign: HTTP 200 containing OAuthException code 10, application-permission hint.
- Ad Set and Ad: HTTP 200 containing GraphMethodException code 100/subcode 33.
- Creative: no independently obtained Creative ID, so no Creative object read
  was possible. The retained Ad contains its inline Creative specification.

These errors do not prove object absence, deletion, publication or a profile
restriction. Every saved fragment and relationship matched its original intent;
the two distinct hierarchies remain confirmed duplicates in one draft container.
Both inspections report mutation_posts=0, editor_verified=false and published=null.

The source-pinned `AdsEditorUnsupportedReasonUtils` module (module SHA-256
`27952b1a6679e1da3f15973d67e56d70fd6bb71e381c9efb6c7eedc8e02993f4`,
script SHA-256 `e2c3d08281745a87850fa38194b220686a2fc3abe8afa239713724eb91add185`)
has an explicit supported-objective set that omits OUTCOME_TRAFFIC. Its `C`
predicate returns `!u.has(objective)`. The Campaign/Ad Set unsupported-reason
functions use this predicate; the Ad branch first considers a promoted object's
full_funnel_objective. This supplies a concrete editor-code incompatibility for
that representation, but does not by itself prove the correct outgoing private
schema or authorize adding an ODAX flag. AdsAPIObjectives itself DOES include
OUTCOME_TRAFFIC. No objective rewrite or new mutation parameter was introduced.

The independent browser again redirected the requested account 1119599940716086
to 1082351911351205, belonging to a different signed-in operator. The local
extension reported that the originating browser context was unavailable. No
terms were accepted, credentials injected, proxy route changed, payment action
performed, or advertising object created in that browser. Correct profile-38
session access remains necessary for actual editor acceptance.

### Validation-settlement correction

Creation previously rejected a nonempty active_errors list but could proceed
when validation_status was HAS_ERRORS without details, or validation was not
settled. The publisher now requires VALIDATED before another fragment or
publication POST. HAS_ERRORS is terminal for that read; absent, pending or
unknown validation remains a retryable saved-read operation. Existing accepted
receipts prevent replay. The UI displays safe Meta error codes and field names
outside the collapsed inspection JSON, separately from the read job's SUCCESS.

Local validation: 176 private-advertising tests passed, including the exact
1815857/bid_amount rejection, missing error details, unsettled reads, no POST
replay, and unchanged selected objective/bid strategy; worker UI JS tests passed.
These are regression tests, not live editor or publication acceptance.


### Budget-owner bidding serialization correction (continuation)

Current GitHub baseline was refreshed to main
`90f0f49ef2ec3c1b55b32aeaea7901624a7b9289` (PR #221). The previously prepared
local diagnostic changes were superseded by PRs #220/#221 and were not replayed.
No new Meta mutation was sent in this continuation.

A confirmed implementation mismatch is now covered: ObjectiveContract validated
only Ad Set.bid_strategy, including for Campaign-budget configurations. The
creation serializer sent that Ad Set choice but no Campaign strategy or legacy
booleans. The retained profile-38 intent has Campaign.daily_budget=500 and only
Ad Set.bid_strategy=LOWEST_COST_WITHOUT_CAP. The previous real native validation
error 1815857 names bid_amount. That error is evidence to investigate bidding;
it does not prove that the correction clears Meta validation or the separate
unsupported-objective editor error.

Reviewed supplied Meta editor source provides the exact model rules:

- AdsUEditorCampaignDeliverySelectors selects CampaignGroup fields when
  hasBudget_LEGACY is true; otherwise it selects Campaign (Ad Set) fields.
- AdsCampaignGroupUtils enumerates bid_strategy, is_autobid,
  is_average_price_pacing and pacing_type as rolled-down fields.
- AdsCampaignGroupBudgetMutationUtils.setCampaignGroupBidStrategyAndLegacyFlags
  sets the Campaign strategy and booleans together. For WITHOUT_CAP: true/false;
  WITH_BID_CAP: false/false; COST_CAP: false/true.
- Its resetCampaignWhenCampaignGroupBudgetEnabled removes these fields from the
  Ad Set. A capped bid_amount stays on the Ad Set; WITHOUT_CAP deletes it.
- AdsLiveCreationDataManager.__createCampaignGroup passes bid_strategy, is_autobid
  and is_average_price_pacing directly into createFragment("campaign", values).
  This connects the reviewed model fields to the actual draft-creation path.
- AdsCostStrategyUtils.getBidLegacyFieldsValueByBidStrategy supplies the separate
  Ad Set booleans (COST_CAP uses false/false there). These contexts are not merged.

The versioned private_ads_bidding_20261021.json and offline source fixture pin all
five complete module factories and the supplied export artifact hash. The new
serializer copies the original selection, transfers strategy/pacing to its
budget-owning level, writes the exact reviewed legacy booleans, and never inserts
an arbitrary bid amount. Contradictory explicit strategies/flags/pacing fail
before bootstrap or media. Objective, optimization, budget, targeting, Page and
creative remain unchanged. The original Launch fingerprint is unchanged; the
native submitted binding and new serializer/manifest digest remain journaled.
Historical accepted publication recovery retains its predecessor's original
serializer and read-only restriction. Existing retained drafts are not rewritten
or resubmitted by this change.

Local verification: 185 private-advertising tests passed, including 9 new bidding
cases, replay/restart recovery, fresh native-binding reads and DRAFT payment/POST
guards. Launch catalog, multi-account batch-review and worker UI JS tests passed.
These tests are offline; live editor acceptance and live error clearance remain
UNVERIFIED. OUTCOME_TRAFFIC was not replaced and no ODAX flag was added.

Current external access limitation: the local Browser tool reports that it
requires the originating browser context. Both connected Railway identities
return no required viewer role for the exact existing service. The operator's
personal Facebook session is prohibited and is not used. No alternative account,
secret extraction, browser-auth bypass, deployment or Meta mutation was attempted
through those denied paths. Current production revision after PR #221 remains
unconfirmed until authorized service/browser access returns.
