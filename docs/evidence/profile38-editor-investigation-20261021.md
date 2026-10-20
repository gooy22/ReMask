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
