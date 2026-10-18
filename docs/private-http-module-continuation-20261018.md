# Private HTTP Sync and complete saved-source inventory

The complete offline pass parses all 26 saved CDN scripts (26,734,999 bytes),
including **15,115 literal module factories**, with no unparsed declared module
names and no conflicting factory versions in this capture. The previous catalog
silently omitted four factories above 180 KB: `ReactDOM-prod.classic`,
`AdsInsightsMetricDefinitionsUngatedStatic`, and the Campaign Age and Campaign
Languages field Relay wrapper queries. Full inventory now retains large factories;
individual network reads still have finite script, total byte and time budgets.

The reproducible source/route/dependency review is in
`evidence/module-coverage-20261018.json`. Run
`python3 python_backend/tools/audit_module_coverage.py SAVED_SCRIPT_DIRECTORY OUTPUT.json`
against the original URL/digest index. No JavaScript is executed. The review
records 182 query artifacts, 23 mutation artifacts, 172 other Relay artifacts,
37 Relay ID modules and 1,288 UI modules. It also records **3,021 dependency names
absent from this saved capture**, including loader aliases. Neither those names
nor the known factories are reported as all Facebook code or implemented actions.
Some installed sender proofs come from other reviewed captures; absent provenance
in these 26 files does not remove an installed adapter or authorize a new one.

## Runtime corrections

- Normal Sync is private HTTP/2 only. It enables source-compiled read-only
  GraphQL inventory queries and retains the assigned profile transport. An
  explicit BM selection disables global Business discovery. Inconclusive reads
  return partial evidence and do not automatically open Chromium. Legacy browser
  observer code remains available to separate diagnostic paths; it is not the
  normal Sync fallback. Existing confirmed inventory remains protected by the
  PHP sync outcome boundary.
- Profile-level auth/proxy/timeout errors survive the worker-to-PHP boundary even
  when no Business has yet been discovered. Proxy errors do not become expired
  session claims. Connection exception strings and credentials are not returned.
- Module discovery inventories every unambiguous advertised CDN JS resource,
  including generic modules and maps found in subsequent scripts. The old
  256-URL ceiling becomes a resumable 4,096-resource queue with explicit limit
  and completeness flags, not an unbounded request inside Launch.
- Bounded windows retain reviewed artifact/sender/reference evidence. A sender
  and its artifact in different files can now establish one exact proof; schema
  changes or competing versions still cannot authorize a new request adapter.
- Failed scripts receive up to three reads; unseen scripts precede retries.
  Pending collection triggers a later ordinary bootstrap even with no expired
  Relay IDs. Exhausted failures never report complete capture.
- Invalid editor metadata cannot prevent collection of unrelated modules.
  Sync documents also feed due source refreshes through the existing profile
  HTTP transport. No startup profile scan or live publishing is introduced.

## Verification boundary

Regressions exercise the real Sync entry point, selected BM scope, auth failures,
proxy failures, failed optional Page enrichment, split-window proofs, resources
beyond the old ceiling, nested maps, retries, large artifacts and incomplete
captures. The entire saved corpus is structurally parsed and its hashes checked.
No real profile, ad publication or bank transaction is used for these checks.
Passing these checks is not proof that a current Meta session can publish.
