# Launch preparation after the Railway migration

The current branch remains `fix/ads-received-response-diagnostics`. The migrated
service uses the same source-backed module inventory and HTTP/2 adapters as the
original image. Inventory alone does not install arbitrary vendor factories as
executable operations.

## Fixed

- Shared source-backed lookup controls now send the current page CSRF token.
  Previously, work, education and other generated lookup fields used a raw POST
  without that token, so the existing login guard returned HTTP 403.
- Private Launch plan/job/status requests and multipart media staging also send
  the current CSRF token. The multipart content type stays browser-generated.
- Creative and Launch invalidate in-flight searches immediately on new input.
  Escape, choosing an option and closing the Creative editor prevent delayed
  responses from repopulating dismissed results. IDs and serializers stay intact.
- Browse truncation and catalog size survive the live template endpoint and the
  workspace reference cache. Creative displays the refine-search hint even when
  the server returns exactly its page limit.
- `/ready.php` requires writable persistent directories, a readable JSON profile
  store and a responsive Python worker. It returns only boolean checks, never
  profiles, credentials or filesystem paths. It does not contact Meta.
- Railway configuration and the Docker diagnostic check use `/ready.php`.
  Existing `/health` remains the lightweight liveness route. Image-based Railway
  services also need the healthcheck path set in their service settings.
- CI runs the picker runtime regressions on the current working branch.
- Bundle saves capture fields, carousel order and card settings before media
  staging starts. Restores verify every returned media ID in request order even
  for older bundles without stored checksums.
- Launch stops before job creation if settings, targets, bindings, delivery or
  selected files changed during staging or review. Automatic discovery of the
  sole masked payment method remains supported. New carousel files replace the
  saved media source instead of submitting two competing sources.

## Validation

The previous release passed all 1,828 backend cases in CI with no skips, plus
the installed editor browser workflow and Docker readiness diagnostic. Local
Chromium is unavailable; browser results come from CI. The current changes add
delayed-upload, changed-form and foreign-media regressions. The complete PHP
overlay build validates the actual Docker installation order. Readiness and PHP
targeting/cache guards remain part of validation.

## Remaining live acceptance

Direct verified-TLS HTTP/2 requests from the destination container reached
www.facebook.com (200), business.facebook.com (302), static.xx.fbcdn.net (302),
and github.com (200), without profile cookies or an environment proxy. No network
block to these endpoints was observed. Successful infrastructure checks do not
establish profile validity, advertising permissions,
funding readiness or publication success. Those require scoped live acceptance
through ReMask. No ad publication, card transaction, profile scan or profile-16
request is part of this preparation.
