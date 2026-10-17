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

## Validation

The complete PHP overlay build passes in Docker installation order. Backend
discovery ran 1,828 cases without failures; 83 Chromium cases were skipped because
this local environment has no installed browser. The Chromium download endpoint
returned an incomplete archive, so no local browser pass is claimed. The existing
browser workflow includes the real editor serializer and newly strict CSRF
fixtures. All eight Node test files pass, including controlled late-response and
authenticated lookup cases. Readiness and PHP targeting/cache guards pass.

## Remaining live acceptance

The destination Railway workspace is still Limited Trial. Its network restriction
must be considered when evaluating authenticated Meta reads. Successful
infrastructure checks do not establish profile validity, advertising permissions,
funding readiness or publication success. Those require scoped live acceptance
through ReMask. No ad publication, card transaction, profile scan or profile-16
request is part of this preparation.
