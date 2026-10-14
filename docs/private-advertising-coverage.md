# Private advertising coverage

The 15,111 captured modules are a dependency and provenance catalog. This count
does not mean 15,111 implemented advertising operations. Request senders remain
limited to reviewed routes; importing a factory or enum never authorizes a new
sender or establishes live account permissions.

| Capability | Creative templates | Launch | Private source / sender |
| --- | --- | --- | --- |
| Campaign, ad set, ad and creative parameters | Reusable controls and structured module fields | Effective payload, overrides, durable bundles | `form_schema`, reviewed draft / fragment / publish routes |
| GEO, languages, interests, behavior, education, work and demographic targeting | Shared reference pickers; no FB/RK binding | Same reference pickers | `TARGETING_SEARCH`, `TARGETING_BROWSE`, `LOCATIONS_READ`, `LOCALES_READ`; source metadata for finite enums |
| Device models, OS versions, manual and automatic placements | Source enum choices | Same choices | Captured private field definitions |
| Potential audience | Live calculation using a server-selected read context | Generic or separate per selected RK | `ACCOUNT_READ`, `DELIVERY_ESTIMATE`; potential-reach selectors |
| Advantage+ and relaxed targeting audience sizes | Display when returned by Meta | Same, per RK | Actual `estimate_mau_advantage_audience_enabled` / `estimate_mau_relaxation_aware` |
| Daily reach, actions and impressions | Actual daily curve and daily budget | Effective targeting, budget, optimization and RK bindings | `AdsBisonUtils`; `daily_outcomes_curve` |
| Bid cap, target cost and cost cap forecast | Source curve interpolation | Same | `AdsBisonUtils`; unknown metrics remain unavailable |
| Campaign budget forecast | Actual campaign group curve required | Same | `campaign_group_curve`; no substitution of an ad-set curve |
| Live placement inventory | Optional read, no template binding | Separate per RK | `TARGETING_BROWSE`; incomplete pagination is rejected |
| Local creative/media preview | Existing template preview | Existing local preview | Workspace media, no Meta write |
| Meta placement-format preview | Resource identities belong to Launch | Separate, scoped, sandboxed previews of existing Meta media / Instagram posts | Reviewed native `CREATIVE_PREVIEW`; Page, image/video/IG ownership rechecked |
| Pages, pixels, custom audiences, conversions, Instagram accounts and posts | Deliberately not stored as template identities | Scoped selectors and bindings | Account scope checks + reviewed resource reads |
| Existing images, videos and creatives | Workspace references only | Scoped asset selection | `IMAGE_READ`, `VIDEO_READ`, `ADS_READ` |
| Image/video/carousel creation | Workspace media / ordered cards | Per-account upload and durable draft launch | `IMAGE_UPLOAD`, `VIDEO_UPLOAD`, draft fragments; no browser worker |
| Publishing and retry/reconciliation | Save only | Explicit review/job actions | `PUBLISH`, `PUBLISH_READ`, `OBJECT_READ`, `FRAGMENT_READ` |
| Campaign/ad-set/ad inventory and performance | Not a template property | Asset manager / job insights | `CAMPAIGNS_READ`, `ADSETS_READ`, `ADS_READ`, `INSIGHTS_READ` |

## Boundaries that must not be disguised as supported operations

- A missing/expired session or unavailable configured proxy produces a visible
  read failure. It does not bind the template to a profile or prevent saving.
- A not-ready estimate never displays the factory's sample numbers. Missing
  bounds, metrics and currencies stay unknown. Estimates are transient and are
  not persisted into templates or Launch bundles.
- A lifetime budget is not converted into a daily prediction without a reviewed
  schedule/pacing contract. A CBO forecast requires a returned campaign curve.
- Reservation/reach-and-frequency bookings, experiment setup, automated creative
  generation, new recommendation actions and other modules without a reviewed
  sender are inventory entries, not implemented operations. They require their
  own request/response and ownership contracts; no invented route is dispatched.
- This change's real Meta response and permissions are not proven by fixture
  counts. Hermetic tests prove serialization, interpolation, stale-response
  handling, failure behavior and request scoping. Fresh session-based acceptance
  is a separate check through ReMask.

Estimate module factories and wrapper hashes are retained in
`python_backend/app/contracts/ads_delivery_estimate_20261014.json` as source
evidence. Only genuine sender responses feed the UI.
