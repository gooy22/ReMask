# Advertising HTTP migration

Launch Review and execution now use the profile-bound private HTTP/2 session.
The page-access endpoint used by Launch also uses HTTP. Neither starts Chromium
or switches to browser transport after a read failure. Cookies, proxy and UA
stay bound to the selected profile; review proofs expire and are invalidated by
credential/proxy changes.

Billing uses the existing versioned READ_ACCOUNT / READ_METHODS doc_id catalog.
Page eligibility currently requires a positive exact-RK promotable-pages response
in the fixed Ads Manager document. Managed Pages, request parameters, missing
responses and partial/error payloads do not establish advertising access. Some
Meta versions defer this connection to an identity query, so the document alone
may be inconclusive. A captured static identity query is still needed for those
versions; there is no browser fallback.

The four CREATE steps still depend on complete server-side mutation contracts.
They are validated before the first CREATE; no numeric doc_id is invented. This
release does not claim those production contracts exist, does not upload media,
and does not establish successful live advertising creation. Error/deferred
responses and IDs in response metadata cannot mark a CREATE successful. BM
context is passed explicitly into every Launch mutation. Durable no-replay
checkpoints remain in effect. Ads remain PAUSED under the existing launch policy.

Fixed the AD_SET/ADSET mismatch in PHP and JS contract status. Linked cards with
explicit Meta confirmation tasks or expired status block Launch with the actual
reason instead of being marked ready.

An opt-in one-shot startup audit can read exact profile/account/business
readiness and report actual contract status. REMASK_LAUNCH_HTTP_AUDIT_SOURCES=1
additionally collects a bounded set of public Ads frontend modules into a
maintenance candidate, never installs contracts or sends advertising/payment
mutations. Both maintenance read endpoints retain existing authentication.

Validation: HTTP-only readiness fixtures, foreign/partial/error responses,
credential-change cache invalidation, card-confirmation gating, mutation result
checks, existing Launch idempotency tests and a real TLS HTTP/2 CONNECT proxy test.
Production evidence must be appended separately after the audit.

Production evidence (2026-10-11): deployment bf5fb02 reached SUCCESS; the health
endpoint returned ok. Profile 16 / RK 120247991367350146 negotiated HTTP/2 on Meta
reads, with browser_started=False and mutation_posts=0 for the advertising audit.
All four CREATE contracts were absent (PRIVATE_LAUNCH_CONTRACT_REQUIRED).
The document page read was inconclusive and static billing reported
PAYMENT_ACCOUNT_QUERY_REJECTED; neither is converted into readiness.

The observed AdsPromotablePageListStoreSourceServerQuery artifact has doc_id
26138679029145319 and variables userId, limit, after, filter. Its sender uses
the selected USER, and the response is user.facebook_pages(is_promotable:true).
This is an actor-wide page list, not an exact-RK advertising permission proof.
It must not be installed as the Launch page-access contract. The source artifact
SHA256 is 79c658d11ac8137e56574376e2a3d4b7ca4f008f6edd3b74a60a4f95da926d26.

Mandatory local build suite: 932 tests passed (18 skips). The broader local
discovery run is not a clean full-suite result: its PHP-dependent checks need
the complete PHP environment. No successful real advertising CREATE is claimed.

Continuation: the server now enforces PAUSED both in the effective payload and
the outgoing mutation template. Existing Launch keys bind a SHA256 of the
effective configuration, Page and masked funding choice. Changed configurations
cannot splice into an earlier chain. SQLite's unique INSERT claims each CREATE
atomically, including concurrent worker processes, rather than overwriting an
existing submit checkpoint.

Every step additionally requires a pinned private READ query contract before
the first CREATE. After a returned ID, the worker saves READ_PENDING with that ID
durably and issues an independent query. Only complete response data matching
the exact object, RK, PAUSED state and preceding object/Page relations can mark
SUCCESS or promote a learned result path. A failed or interrupted read retains
the ID in RECONCILE_REQUIRED and cannot replay CREATE. Query IDs and response
paths are operator-reviewed contracts; the release supplies no invented Meta
IDs. Legacy SUCCESS records without immutable configuration and independent
READ evidence cannot silently resume.

Public source maintenance now follows only literal, unambiguous Meta Bootloader
maps and observed CDN URLs. It retains dependency lists, prioritizes core
publication modules, has download/time/source budgets, and never executes JS.
Exact module selection can inspect already saved public code without another
Meta request. Empty failed capture no longer overwrites previous evidence.

Production source evidence: the HTTP capture read 80 public scripts and retained
4376 advertising modules. adsDraftPublishingPublishAction calls
AdsDraftPublishDataManager.startPublish(draftID, fragmentIDs, isCurrentlyPublishing,
{ignore_errors, include_fragment_statuses:true}, fakePublishID, creationSource,
shouldPublishImmediately, isFirstBlockingError). The create-fragment flow calls
startCreateFragmentAndPublish. These are draft publishing entry points, not
evidence for four guessed independent CREATE doc_ids; the actual request sender
and resulting object READ artifacts still need to be pinned.

The subsequent profile 16 Business HTTP preflight returned BUSINESS_LOGIN_GATE
(an observed /business/loginpage route). Advertising source reads themselves
used HTTP/2; advertising mutation_posts stayed zero. Restoring that profile's
Business HTTP session is required before a live PAUSED canary. The current
creative library is empty and no saved Launch bundle is selected.

Continuation validation: the final mandatory suite ran 943 tests successfully
(18 skips). Independent READ fixtures exercise all four stages with eight
separate private requests, foreign RK/ID/state/parent rejection, missing
downstream READ contracts and durable no-replay after an unconfirmed READ.
This is code verification; no real production advertising CREATE is claimed.

Further continuation: a temporary independent READ failure can now resume using
the already returned exact entity ID and unchanged configuration. Only READ is
repeated for that step; CREATE is never replayed. Unknown mutation results,
wrong RK/ID/PAUSED/parent proofs and changed configurations remain blocked.
A crashed READ_PENDING can be retried after 90 seconds, beyond the original
40-second query timeout. Conditional SQLite updates prevent a late failing
query from overwriting another worker's confirmed result. The retained ID is
also present in the ActionResult returned to the application.

Saved-source inspection confirmed that AdsDraftPublishDataManager and
AdsGraphAPI are absent from the current captured snapshot. Their UI callers and
the publishing-init reducer exist; these do not establish a request contract.
Dependency traversal now follows each preferred publication root before
unrelated UI roots. Candidate records retain the exact module wrapper as prefix,
factory and suffix, without storing the factory twice. Captures preserve the
observed public script catalog and report discarded-source counts. Inspection
reports missing modules/dependencies and export-budget omissions explicitly.
The authenticated candidate endpoint's file limit now matches the 20 MB reader.

The saved-only inspection deployment f0f62b51 reached SUCCESS. It made no
advertising audit request to Meta. An existing card verification task separately
reported profile 16 SESSION_EXPIRED with post_not_sent=true. Actual advertising
request contracts and a successful live PAUSED publication are still pending.

Continuation checks: the mandatory local suite ran 948 tests successfully
(18 skips). After adding downstream-parent and interrupted-query recovery
fixtures, all 49 focused Launch/source tests passed. Temporary READ recovery
does not replay the already submitted Campaign or Ad Set mutation.

Deployment 9f96e3f6 of commit 4ad9e2d reached SUCCESS and health returned ok.
Its saved-only selection report positively confirmed the absence of the
publishing sender, providers and AdsGraphAPI from the snapshot. Candidate
collection now also follows lowercase ads publication components, XAds
controllers and explicitly selected generic transport helpers. Source loading
still uses literal observed URLs and factories, and never executes JavaScript.

Temporary READ failures also expose retryable=true at the Job boundary. The
existing Retry Failed action preserves the task idempotency key and Launch key;
the resumed step sends only its pinned READ. Foreign-object proof failures
retain retryable=false. A JobStore integration fixture covers the entire
failure, retry, unchanged-key and query-only recovery path.

Continuation on 12 October rebases the pending proxy/capture work onto release
04841b1 (PR #127). Private HTTP rejects missing profile proxies before dispatch.
A bootstrap connection failure is a sanitized transport error, never evidence
of expired cookies, and does not retry other Meta origins or DTSG refresh.

Advertising source collection now prioritizes the exact sender dependencies
observed in the saved startPublish/startCreateFragmentAndPublish callers and
can inspect up to 400 literal observed script URLs, with three workers and a
75-second deadline. Concurrent reservations enforce the 50 MB byte budget;
unknown partial reads consume their reserved budget. Proxy/connection/auth
failures cancel the batch and preserve the previous snapshot. Candidates
remain separate from the deployed static advertising manifest.

Review no longer collapses distinct payment credentials with identical masked
brand/last4 into one automatically selected card. A missing previously selected
card requires a new explicit choice and clears the stale chooser state. Local
media and carousel data cannot bypass pre-CREATE validation by omitting format.
The initial PAUSED policy and independent READ/no-replay recovery are retained.
The new UI cache tag is 20261012-advertising-worker-v3.

Real publication CREATE/READ contracts are still absent from the deployed
manifest. These changes prepare reliable collection and fix the launch input
and transport paths; they do not establish a successful live publication.
No live Meta request is needed for the regression fixtures above.
