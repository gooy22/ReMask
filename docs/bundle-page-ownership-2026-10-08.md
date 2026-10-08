# Separate Page ownership for each Prepare bundle

Live job `06e3f75114a54779ba4607382626c0de` confirmed both Businesses and ad accounts before failing on Page ownership:

| Business | Confirmed ad account | Page before this fix |
| --- | --- | --- |
| 1428816905866955 | 120251352568830122 | 1348798761652037, full rights confirmed |
| 1451903470239662 | 120251650486340295 | The planner incorrectly selected the first Business's Page |

The operator selected **one Page per Business**. A Page cannot be simultaneously owned by two Businesses. The existing `Add an existing Facebook Page` contract remains the ownership operation; sharing advertising access does not satisfy Prepare's full-control requirements.

## Behavior

Prepare retains existing BM/RK objects and repairs each bundle before advancing. RK creation does not implicitly invoke the legacy common-Page access flow. Page access resolves a durable `(Facebook actor, Business)` binding, reuses an eligible exact Page after the pinned ownership read, or creates a separate `PrgssTeam` Page over the existing private HTTP transport. It then claims the Page through the pinned Add existing mutation and independently verifies Page ownership, operator full Page rights and full RK rights.

The first legacy Page and its grants migrate only to their confirmed owner Business. The profile's default display Page remains unchanged. SQLite uniqueness prevents binding a Page to another Business of the same actor; allocation does not silently transfer the first Page.

Each additional Page CREATE uses a stable actor/Business checkpoint across jobs, slot reorderings and local profile aliases. Same-name Page CREATE reconciliation is scoped by Business, with unscoped legacy ambiguity retained. Every previously allocated Page is included in the CREATE baseline even if a transient inventory omitted it. Page creation does not call the legacy browser attachment helper. Retained Page-access submits are pinned to their exact previous Page before reconciliation.

Prepare's per-bundle output includes Page IDs and readiness checks use the exact Page/BM/RK triple. The interface shows each complete triple and advertises the correct Page count.

## Validation and limits

New offline regressions exercise first-Page migration with pending grants, SQLite uniqueness, actor isolation and aliases, parallel retries, ambiguous Page CREATE recovery, same-name scoping, baseline preservation and no browser attachment. A composed Prepare → service → fan-page handler → PageAccess → static ownership/rights test repairs two existing BM/RK bundles using only Page2 creation and its claim/assign operations. A lost claim response reconciles successfully; a repeated Prepare with reversed Business selection emits no further mutations.

These tests use synthetic Meta responses in observed query shapes. They do not prove that a newly created Page has been accepted by live Meta. Deployment startup and regression evidence must be reported separately from a live Prepare result. Meta session requirements and platform account limits remain authoritative.
