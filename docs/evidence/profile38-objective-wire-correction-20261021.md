# Profile 38: private Traffic Campaign representation

Offline implementation evidence, 21 October 2026. Editor state remains
`EDITOR_UNVERIFIED`; no live correction or editor acceptance is asserted here.

The Launch selection is `OUTCOME_TRAFFIC`. The reviewed private Ads Manager
CampaignGroup record uses `LINK_CLICKS` together with
`is_odax_campaign_group: true` for modern Traffic. This is a Campaign objective
representation, independent of the Ad Set `optimization_goal`. The prior direct
copy of `OUTCOME_TRAFFIC` reached fragment validation but was absent from the
reviewed editor's supported-objective set.

The versioned `private_ads_objective_wire_20261021.json` pins the sources:

- `AdsODAXObjectiveStrings` calls CampaignGroup `LINK_CLICKS` Traffic.
- `AdsODAXUtils.isODAXTrafficCampaignGroup` requires exactly true plus
  CampaignGroup `LINK_CLICKS`.
- `AdsEditorUnsupportedReasonUtils` allows this objective and rejects
  `OUTCOME_TRAFFIC` in its Campaign/Ad Set editor checks.
- `AdsLiveCreationDataManager.__createCampaignGroup`, retained in the existing
  primary-source bidding fixture, writes `objective:n.objective` and
  `is_odax_campaign_group:n.isODAXCampaignGroup||null` into the same actual
  `createFragment("campaign",s,...)` request. This establishes the flag's
  creation role, rather than treating a read/display field as a write contract.
- The CampaignGroup record accessors expose both writable fields; the draft
  apply-changes reducer merges the changed record and `AdsDraftFragmentDataManager`
  passes typed `values` through to `addraft_fragments` without objective mapping.

The small creation serializer now applies that representation only after the
existing objective/optimization validation. It leaves the saved Launch, its
fingerprint, targeting, optimization, billing, budget, bidding, and status
unchanged. PR #222's bidding serializer still runs separately. Other objectives
are unchanged. Local plan output shows selected and private objective fields
and explicitly says `EDITOR_UNVERIFIED`.

Historical publication recovery bypasses the new serializer. Existing retained
attempts are inspected using their exact saved bindings; their old objective is
never rewritten. The reviewed `3a225cf` sender digest
`9b2b414e40aa4d6a1d69dfdfefee07341a45fa18b4328a72f185e63c28d00c44`
is allowed for read-only inspection, never for resuming old writes. Regression
coverage explicitly checks old objective retention, unchanged journals and zero
POSTs after this release.

The remaining acceptance is a controlled, explicitly identified new corrected
hierarchy (or a separately reviewed repair), real validation of every fragment,
and an authorized browser session opening the exact profile-38 account and
Campaign/Ad Set. This source-backed serialization is not proof of editor
acceptance or publication readiness. No existing draft has been modified by
these tests.

## Retained Campaign repair

An explicit `repair_objective: true` operation now applies only this correction
to the existing controlled hierarchy from sender `9b2b414e…`. It requires the
original key, Launch fingerprint, actor, profile/Business/account/Page scope,
all three exact retained fragment receipts, and an unpublished PAUSED draft.
Before the single write it reads account and USER draft ownership and all
three full fragment specifications, identities and parent relationships. Each
fragment must still be EDITING with no publish state. A failed Ad's object ID
may be recovered only from its exact saved fragment receipt and fresh read.

The only POST is `action: modify` for the same Campaign object ID, containing
exactly `objective` and `is_odax_campaign_group`, with explicit original
`old_value`s. No Campaign/Ad Set/Ad creation, child change, upload, billing or
publication request is routed. Its deterministic separate repair journal
retains an accepted fragment receipt; restarts/pending validation resume GETs.
Unknown replies and changed contracts fail closed without replay. Original
journals remain byte-for-byte unchanged.

Completion proves only that the corrected Campaign fragment retains its exact
other settings/identity and is VALIDATED. Fresh child reads are reported
separately, including retained Ad 1359188 diagnostics. `all_fragments_validated`
remains false when any child has errors, and editor/publication readiness
remain false. Ordinary inspection of the original journal still expects the
original Campaign fields; use the explicit repair operation to reread its
corrected state. No retained live object was changed during these offline tests.
