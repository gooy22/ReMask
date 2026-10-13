# Private Creative and Launch completion

Base: Railway/main `f0f3f2746e8d3da4d3ed3e5266f37fb54f44c6d0` (PR147). Integrates the private advertising work from PR133 while retaining the newer main account inventory, pixel, direct-test lease, typed rejection, publication journal and funding guards.

The installed Creative and Launch metadata now comes from reviewed private Ads Manager modules, through the local authenticated worker, rather than SDK schema endpoints. Source-backed enum and field evidence is retained in the advertising catalog. New schemas and senders require explicit review; the existing 14 artifact/sender-bound doc_id refresh contracts are unchanged. Historical payment/settings ID proposals from PR133 are not installed by this change.

Audience estimates and placement browsing use their captured AdsGraph read senders. Preview uses the captured LWI AsyncRequest route and fresh in-memory request envelope. All live Meta/CDN reads retain the common strict HTTP/2 transport and exact profile/account scope. No advertising Chromium dependency, public SDK fallback or guessed doc_id is introduced. Unknown preview markup and account mismatches fail closed.

Private advertising supports images, serial video upload, mixed-media carousels and eligible existing Instagram posts. Existing creative assets and advanced asset-feed media are independently checked against the account before mutation. Draft values remain typed JSON; publication uses captured top-level flags and explicit success. Known publication recovery performs reads only; unknown POST replies remain non-replayable. Creation starts PAUSED, with delivery changes gated by independent hierarchy verification.

Launch bundles retain durable media IDs, checksums, ordered card settings, advanced parameters and daily/lifetime budget period. Loading a bundle clears the old media first and refuses unavailable or changed media. Edited primary fields remain authoritative over preset defaults. Shared media is not implicitly deleted when a preset is edited/deleted. UI modules receive content-derived cache revisions.

The deployed UI grouping, labels and responsive workflow from PR146/147 remain in place. Existing funding and direct-test lease restrictions remain in place.

Offline verification covers scoped reads, uploads, wire contracts, native previews, form schema provenance, advanced values, durable claims and installed PHP/JS generation. Browser fixtures exercise installed Launch save/reload, budget editing, lifetime budget, unavailable media and ordered mixed carousels. CI and live ReMask results must be checked on the final commit before claiming production success. Source review alone does not establish successful Meta publication or delivery.
