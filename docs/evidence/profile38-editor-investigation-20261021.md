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
