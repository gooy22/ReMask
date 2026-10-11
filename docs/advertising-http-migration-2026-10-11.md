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
