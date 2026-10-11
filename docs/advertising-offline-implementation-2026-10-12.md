# Advertising implementation prepared without live Meta checks

This change is an offline implementation pass. The user reported that the proxy
expired and asked to defer checks and fixes until later. No live Meta requests,
test runs, CI validation or production deployment are claimed for this change.
It belongs on the advertising branch until the validation pass is complete.

## Implemented application paths

The existing Launch now has a preparation endpoint that builds an ordered plan
without resolving a Facebook profile or contacting Meta. Plan preparation never
returns live readiness. The same form supplies the desired delivery state,
per-account overrides, media references and publication parameters to its Job.

Local images/videos resolve through the authenticated internal PHP Media Library
bridge. The existing store factory handles file storage and PostgreSQL content
materialization. Browser-provided paths and URLs are not accepted. The worker
checks the managed directory, size, MIME declaration and SHA256 before streaming
the file over its profile-bound HTTP/2 connection. Single media, ordered carousel
cards and existing Instagram posts have separate configuration paths.

The publication driver accepts either the four existing CREATE contracts or one
observed PUBLISH_FAMILY contract. Family publication must return all four exact
IDs and independently query every object with the correct account, parents,
Page and PAUSED state. It cannot switch publication strategy inside an existing
Launch. This accommodates the observed draft-publishing entry points without
assuming that Meta necessarily uses four independent CREATE requests.

Creation remains PAUSED. Desired ACTIVE states are applied after publication and
verification, in Ad -> Ad Set -> Campaign order. Existing Job objects can also
receive a separate private_delivery task. Pausing orders Campaign -> Ad Set ->
Ad and does not require a billing check. Delivery targets come from the saved
source Launch, not client-supplied object IDs. A fresh typed READ confirms their
account and relations before any status mutation.

The complete plan binds its configuration, Facebook actor, contract definitions
and required READ queries durably. Media uploads, family publication and delivery
changes use atomic submit claims. Returned references survive a READ failure.
Retry resumes queries; unknown CREATE/upload results cannot automatically replay.
A saved delivery success receives a fresh query when resumed, so a later pause
cannot silently be reported as ACTIVE.

Advertising resource queries cover Pages, Pixel, custom audiences, images,
videos, creatives, Instagram accounts/posts, conversion goals, targeting search,
languages, Campaigns, Ad Sets, Ads and insights. Every query uses a reviewed
manifest, bounded pagination and an exact-account response proof. The UI uses the
existing PHP routes and profile-bound worker rather than token/OAuth requests or
a transport pool that switches Facebook profiles. Browsing uses a durable cache;
refresh is explicit. Historical cached data is never live permission proof.
Selected media, Pixel, audiences and Instagram assets require fresh scoped READs
before publication, independently of the browsing cache.

## Manifest interface and remaining evidence

`python_backend/app/contracts/meta_launch_20261011.json` remains intentionally
empty for real requests. Its optional `operations` catalog accepts IMAGE_UPLOAD,
VIDEO_UPLOAD, PUBLISH_FAMILY and ACTIVATE_CAMPAIGN/ADSET/AD. `reads` holds resource
queries; `creative_formats` declares the formats supported by the actual sender.
These declarations never enable a missing request contract.

Each entry needs artifact/sender/READ source hashes and an observation date.
Mutation contracts need the observed doc_id, friendly_name, endpoint, variables
template and exact output paths. Queries need exact identity/account/relationship
paths. Video upload also requires a verified processing-ready state. File upload
currently supports an observed single multipart GraphQL request. A chunked or
non-GraphQL Meta uploader would require its actual protocol adapter; it must not
be represented as an invented GraphQL request.

Available template values include profile_id, actor_id, business_id,
ad_account_id, page_id, entity IDs, client_mutation_id, payload sections, media
metadata and target_status. A resource query also receives query, limit and
cursor. Cookies, FB auth tokens and request context stay inside ProfileSession;
they are never stored in the contract manifest.

The saved Meta source confirms callers of AdsDraftPublishDataManager, but not
that sender's request envelope. It also does not establish exact-account Page,
media-upload, family-publication or activation contracts. The earlier actor-wide
promotable-Page query cannot substitute for account-specific advertising access.
Numeric IDs in tests are fixtures, never production contracts.

Before deployment, run the offline backend/UI/runtime tests, confirm the actual
multipart or chunked uploader and publication protocol from source evidence, pin
the corresponding manifests, and perform the agreed live canary once a working
proxy/session is available. Synthetic tests cannot establish real Meta support.
