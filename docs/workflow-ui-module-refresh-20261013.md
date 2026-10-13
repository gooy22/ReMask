# Creative and Launch workflow, HTTP/2 and automatic module refresh

The Creative editor now separates campaign identity, budget, optional conditions,
ad-set optimization and schedule. Its original controls, IDs and serializer remain
in place. Primary labels and common enum display text are localized; enum values,
budgets in minor currency units, JSON overrides and preset hydration are preserved.
A summary displays actual form values. Advanced fields remain searchable.

Launch has step navigation, grouped headings and accessible profile checkboxes.
Checkboxes update the original multi-select and dispatch its existing change
handler. They do not load Meta, review, submit, activate or alter payment readiness.
The advanced settings panel is inside the main workflow, before review.

## Transport audit

`FacebookWebSession` and `AdsManagerPrivateHttp` use the profile-bound
`PrivateHttpClient`. Facebook origins prohibit HTTP/1; the advertising sender also
checks the negotiated response protocol. Public `*.fbcdn.net` module reads now
use the same HTTP/2-only transport, without Facebook cookies. The proxy CONNECT
protocol is separate. Unrelated proxy diagnostic destinations retain their own
protocol support. No proxy failure enables direct routing or a POST replay.

The existing TLS/HTTP/2 test through an HTTP/1.1 CONNECT proxy verifies the origin
negotiation. It is not a live Facebook publication test.

## What refreshes automatically

Successful normal profile bootstrap, Business precheck and scoped Ads Manager
bootstrap now trigger bounded source collection from CDN resources advertised by
the authenticated document. There is no startup scan of saved profiles, checkpoint
retry, Chromium, JavaScript execution, credential logging or raw-source storage.
Limits: 24 scripts, 2 MB per script, 12 MB total, 12-second wall clock cap. Cache
identity includes the profile, actor, proxy and document resource URLs, with a
30-minute proof lifetime and a 60-second retry backoff after an incomplete read.

14 installed operations have reproducible source baselines across Settings,
Page creation/access, RK creation and payment reading/saving. A new persisted ID
is used only if the complete captured artifact and at least one captured sender
match after normalizing only that quoted ID. Literal module imports, variables,
defaults and response selections remain part of the fingerprint. Changed or
ambiguous code is quarantined with `PRIVATE_MODULE_SCHEMA_CHANGED` before durable
intent or POST. Missing evidence cannot authorize an update. Fresh session
credentials and selected account identities still come from the current profile.

The versioned manifests remain the installed source of payloads and parsers.
The UI SDK field catalog is still pinned; it is not a private-module capture.
The Ads Manager Graph transport is still the reviewed origin/version (`v22.0`);
its fresh session configuration is read on each open. New routes, schema changes,
uncovered operations and new advertising modules require extending the existing
maintenance compiler and source fixtures. This change does not claim automatic
installation of arbitrary Facebook code or full coverage of every module.

## Verification

Backend cases cover ID-only acceptance, artifact/sender/schema drift, ambiguous
versions, expired proof, unavailable proxy, foreign CDN redirects, profile cache
isolation, HTTP/2-only CDN routing, cookie isolation, and the actual existing
GraphQL sender using one updated ID without changing variables or repeating POST.
Browser fixtures exercise the actual Creative serializer, reopen/preset hydration,
mobile layout, profile selection synchronization and dynamic field search.
The production runtime overlay is checked by the complete Docker-order PHP build.
