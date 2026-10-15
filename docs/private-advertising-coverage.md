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
| Daily/bid/campaign forecasts | Removed from the audience panel | Removed from the audience panel | Legacy worker contract retained; editors send `include_forecast=false`, so no curve interpolation runs |
| Placement choices | Automatically installed from reviewed module metadata | Same, before selecting a profile | Captured private placement schema; diagnostic read buttons removed |
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

Potential audience loads automatically on opening/restoring a template and on
changes to its effective settings. Not-ready Meta responses poll after 2/5 seconds
then every 30 seconds; failed reads back off after 15/45 seconds to once a minute.
Only visible editors poll. Changed targeting and a closed editor
cancel pending work. There are no audience calculation or placement verification
buttons. Only returned Meta counts/bounds are displayed, not locally derived
daily outcomes. Read failures remain independent of template editing/saving.

New profiles without a saved RK catalog are also eligible as automatic reference
sources. The worker discovers an existing BM/RK through private HTML/Relay reads
and confirms the exact pair with a fresh account read before fetching targeting
or audience size. Discovery has an 18-second budget and the full read has a
32-second budget. It does not start Chromium, create assets, change transports,
or write a publishing binding. Private process hints expire after 30 minutes;
changed cookies, proxy or user agent require a new context. Banned/excluded
sources are skipped before discovery. Source failures temporarily yield to the
next eligible profile; unknown audience sizes are never fabricated.
