# Advertising source capture from the signed-in Ads Manager

The browser reached the campaigns page after user authentication. This is
separate from the expired profile-proxy audit and does not establish that the
server can use this browser session. Browser credentials were not exported.

The DOM export retained public CDN script URLs and literal Bootloader resource
and component maps only. It excluded document HTML, request headers, cookies,
authentication values and account initialization data. The browser result
serializer truncates large objects and strings: the complete map was therefore
returned as checked 50,000-character JSON chunks before writing it to disk.

Observed: 504 loaded script URLs, 1,030 advertising/transport components and
2,767 public resource URLs. The bounded, unauthenticated CDN collector requested
600 scripts: 596 were captured and four exceeded the body budget. AST extraction
retained 40,521 distinct named modules. All 16 publication targets were found,
including AdsDraftPublishDataManager, AdsGraphAPI and the complete GraphAPI
sender chain that was missing from the previous snapshot.

The collector submits no advertising action. Browser network bodies were not
available, so this evidence does not claim a measured count of the browser's
background mutations or a successful advertising publication. No Publish or
activation action was clicked for the collection.

## What the actual publisher implements

The reviewed startPublish caller submits a draft's fragments through
AdsGraphAPI, GraphAPIRequestSpec, GraphAPICore and ApiClient. The public code
constructs versioned Graph paths, handles access_token and withCredentials,
and supports form-encoded or batched requests. It is not a Relay CREATE
operation with a persisted doc_id. A separate strictly projected DOM inspection
found GraphAPIConfig.adsApiVersion=v22.0 and the default UrlMapConfig.graph
origin https://adsmanager-graph.facebook.com. No authentication values were
extracted. Actual network routing and any SDK graph-domain override are still
unconfirmed; the declared default origin is not a recorded request URL.

| Stage | Source-defined path after version prefix | Evidence |
| --- | --- | --- |
| Create/reuse draft | act_{account}/addrafts | AdsDraftDataManager |
| Create fragments | {draft}/addraft_fragments or addraft_multi_fragments | AdsDraftFragmentDataManager |
| Create and publish | {draft}/addraft_fragments_with_publish | AdsDraftFragmentDataManager |
| Publish existing fragments | {draft}/publish | AdsDraftPublishDataManager |
| Poll publication | addraft_{draft}/addraft_publish_statuses | AdsDraftPublishDataManager |
| Upload images | act_{account}/adimages, multipart | AdsBulkUploadImages |
| Upload videos | act_{account}/advideos; start/transfer/finish phases | AdsVideoUploadUtil and request managers |

Publication returns an async_request_set_id. Its terminal success enum is
SUCCEEDED; per-fragment success is SUCCESS. A success flag or request-set ID is
not proof that every object was created. Poll responses, per-fragment outcomes,
and independent object/account/state/parent reads remain necessary.

Video upload exposes start/transfer/finish fields, server-provided offsets and
separate encoding progress. Receiving video_id at the start is not completion.
The start/transfer/finish path also references the Graph access-token mechanism.
Generic resumability and alternative upload branches need additional review.

Status-switch actions change the relevant draft fragments' status and enqueue
instant publication. The reviewed Ads Manager UI does not supply evidence for
three guessed independent ACTIVATE_* Relay mutations.

## Relay artifacts reviewed

The existing offline AST evidence index reviewed 45 selected modules and
identified 12 persisted operation candidates. The machine-readable record
contains their literal IDs, variables and server response fields, alongside
module/script hashes and observed public URLs. Every candidate remains
ineligible for production Launch.

- AdsManagerLiveDataCampaignQuery still returns server IDs/type names; the
  account, advertising specification and delivery proof remain client resolved.
- useAdsPromotablePagesForAccountV2Query returns an actor-wide Page connection.
  Its account-specific eligible-Page intersection uses client resolvers. Passing
  adAccountID does not make the returned connection exact-account proof.
- AdsAdgroupAuctionPageFieldPromotablePageListQuery has actor-wide
  has_create_ads_access, without exact-RK permission proof.
- useAdsUGPRecommendationCreateDraftFragmentsMutation creates recommendation
  draft fragments, not an arbitrary full Campaign/AdSet/Creative/Ad publication.
- AdsNoDeliveryStatusPostPublishResolutionMutationExecutorMutation handles a
  post-publication resolution; it is not a CREATE or activation contract.
- AdsDraftRealtimeSubscription supplies publish events/messages. It is not an
  HTTP polling query or independent advertising-object verification contract.

The matching evidence is
[advertising-browser-source-review-20261012.json](evidence/advertising-browser-source-review-20261012.json).
It contains facts and schemas, not full vendor source files or credential values.

## Reproducible collection

Use python_backend/tools/collect_browser_ads_sources.py with the sanitized DOM
export and an explicit output directory. It requires the existing backend
aiohttp and tree-sitter dependencies. It reads only validated observed HTTPS
fbcdn.net JS URLs, refuses redirects and uses bounded workers/time/body bytes.
It may use the execution environment's public outbound gateway; it never changes
the required proxy behavior of the operational Meta profile transport.

The full research snapshot is deliberately separate from the deployed
maintenance reader and its 20 MB limit. It must not be copied into the deployed
advertising manifest. The focused metadata report has no executable contracts.

## Remaining evidence

The next step is an actual request/response recording for the publisher,
actual network routing/version confirmation without exporting secrets, and independent
object verification. Under the cookie/private Relay/doc_id requirement, this
captured Graph publisher cannot be silently installed as a substitute. A full
Relay sender, if present, must be proved separately. No operational transport,
proxy guard, production contract or deployed service was changed by this work.

No new backend test suite or live publication probe was run. The public-source
collector and offline AST index were executed to produce the research data.
