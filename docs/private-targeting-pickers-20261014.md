# Private targeting pickers

Creative and Launch now browse choices on focus, including an empty query. Both
use the universal template lookup endpoint and preserve the original serializers:
country ISO codes, numeric region keys, interest/behavior IDs and integer locales.
Lookup identities never become Launch destinations. Saved language IDs reopen
with labels from the recovered mapping. Russian is ID 17; Ukrainian is ID 52.

The installed reference contains 72 locale IDs and 406 location values, extracted
as literal data from AdsAssetFeedLanguageMappingsStatic,
AdsCountryGroupConfig.dbcigar and AdsCountryRegionsConfig.dbcigar. Each source has
module/script SHA-256 evidence. Country labels are localized with Intl.DisplayNames;
English aliases remain searchable. Unequal region-name/ID arrays are omitted.
Cities and omitted regions still use the reviewed private live location sender.
The installed reference describes IDs, not live publishing eligibility.

Normal authenticated Meta bootstrap also collects these exact reference modules
within the existing bounded HTTP/2 source refresh. Complete literal maps can
refresh the reference atomically; spreads, executable values, conflicting tables
and conflicting reverse locale maps cannot replace it. A language-only capture
can refresh languages without erasing geography. No startup profile scan is added.
The template endpoint prefers a fresh compiled reference for 30 minutes, with the
installed source-backed reference available when fresh evidence is unavailable.

Empty interest/behavior queries now call the recovered AdsUnifiedBrowseTreeDataSource
`targetingbrowse` route. Headings without numeric IDs are not selectable; parent
paths are retained. Typed queries continue through `targetingsearch`. Browse reads
reject partial pagination and conflicting nodes, return bounded selections, and
mark truncation. Cached browse rows can answer later text queries without another
profile read. Cache results never establish live advertising permissions.

Saved text-search responses are also indexed by dictionary type, so a later
substring search or empty picker can reuse those real rows without a complete
browse tree. IDs are deduplicated, types remain isolated, and the existing 30-day
expiry applies. Legacy entries acquire type metadata on an exact cache hit;
unidentified legacy query hashes are never guessed. Both editors show the saved
timestamp and mark stale results. A cache miss still requires Meta: captured
sender modules do not contain the complete interest catalog.

Business login responses stop automatic audience retries and cool down the read
source. The cooldown retains the typed failure for eligible sources, so a login
page is reported as a session problem rather than a network outage. Cached rows
are returned before source selection, including while that source is blocked.

One unreadable profile catalog no longer aborts selection of other sources.
Authentication/proxy failures cool down that source; query-specific failures do
not disable unrelated dictionaries. The deployment excludes profile 16 from
reference reads. No advertising or payment mutations are part of this change.

Validation: PHP targeting/reference cases, backend browse and literal-compiler
regressions, existing module refresh tests, complete production overlay order,
and JS syntax checks. Live verification is recorded separately after deployment.
