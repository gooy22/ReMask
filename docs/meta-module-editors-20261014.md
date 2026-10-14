# Private Meta modules and editor adapters, 2026-10-14

The installed source inventory includes **15,111 complete module wrappers** from
26 previously observed public CDN scripts. Each entry records a module digest,
its literal dependencies, the script digest and observed URL. The inventory
contains no browser session, profile data or executable JavaScript. The offline
compiler (`python_backend/tools/build_editor_modules.py`) reproduces the catalog
from those scripts and URL/digest index. A smaller observed fixture reproduces
all editor bindings and 195 nested field definitions.

The normal authenticated private HTTP/2 bootstrap now inventories every parsed
module, including dependencies outside the former Ads-name filter. Settings,
Billing, Business, targeting and device resource maps also participate in deferred
resource discovery. Collection still uses the assigned profile proxy and existing
cookie isolation. It reads at most 24 scripts / 12 MB / 12 seconds per attempt;
256 advertised URLs are queued in bounded windows. The cursor advances by actual
attempts, with a 60-second pause between unfinished windows, instead of repeatedly
reading only the first 24 URLs. No saved-profile scan or browser is started.
Module inventory and partial metadata updates merge under a lock and persist
atomically; incomplete reads retain installed values.

The shared schema compiles 46 source-backed selection controls, 28 unified
reference families, 1,939 exact device strings, OS versions/minimum-version
encoding, numeric education/relationship choices, pacing, optimization subevents,
buying type, app-install state, network selection, placement enums and applink
handling. Source examples and MOCK_DATA are never reference IDs. Both editors
share the same metadata and controls; numeric arrays, id/name objects and string
arrays keep their actual payload representation. Hydration writes the original
serializer elements, which repaint selected choices without separate state.
Launch previously marked OS/device fields covered without rendering them; both
are now present in the generated form. Lookups cover every source-advertised type
that also exists in the reviewed targeting field catalog.

Complex fields use the same recursive editor on both screens. Lists, objects,
strings, numbers, booleans and null values are retained through editing and bundle
restoration. Known nested keys come from literal field modules. Choice controls
replace generic manual ID inputs. The publishing account's assets remain scoped
to its existing destination controls and permission checks.

Reference browsing does not require the account to be publishable. Its fresh read
still proves the exact profile/account/Business relation; it cannot satisfy Launch
funding, readiness or publication permissions. Active sources rank ahead of
unknown/inactive sources. Excluded profiles (including configured profile 16),
authentication walls, proxy failures and foreign scope checks remain enforced.

**Coverage boundary:** inventory is not execution. New or changed arbitrary vendor
factories do not become request adapters. Installed Relay writes continue using
exact artifact-and-sender fingerprints; new write routes require source-backed
adapters and response verification. A module index is not a successful live
catalog response. Interest/behavior/work/education search uses the actual reviewed
account-bound targetingsearch/targetingbrowse sender; unavailable authenticated
transport yields an error, never invented selectable data. This work is verified
offline and does not publish advertisements or inspect real Meta accounts.
