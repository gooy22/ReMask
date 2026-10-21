# Profile-bound public module evidence reuse

The collection-window cache includes the authenticated document's host/path and
complete advertised script set. On main `fed13c7`, three controlled windows
advertising `[A]`, `[A]` on a different page, then `[A,B]` fetched A three times
and B once. Each response used the existing 19,520-byte public source fixture:
4 GETs / 78,080 fixture payload bytes. The same windows after this change make
2 GETs / 39,040 fixture payload bytes. These are deterministic offline fixture
counts, not compressed network bytes, proxy billing or a live lifecycle result.

PR #199 was reviewed at `2122c2988bab457f69e77063b93b8c3c98cefc61`. It modifies
inventory/fast-sync paths and does not contain this module-source cache. Much
of that architecture already exists on main; the PR is not merged or enabled
by this change.

`module_source_cache.py` reuses only parsed public CDN factories, their source
hashes, and observed deferred public URLs. Its key binds the profile name, actor
and proxy before hashing the public script URL. Different profiles, actors and
routes never share entries. It stores no private HTML, cookie or credential.
The in-memory LRU has at most 64 entries and 8,000,000 serialized parsed-evidence
bytes; this bound is not a claim about total Python process RSS. Existing HTTP/2,
profile proxy, cookie isolation and authenticated-document gates remain intact.

Source locks coalesce concurrent reads of the same URL in different windows.
Cancelling a waiter cannot cancel the owner; a cancelled/failed owner publishes
no partial evidence and another waiter may perform its own bounded GET. Failed,
foreign-redirected or oversized responses are never cached or installed. New
URLs and expired entries require new reads. Changed factories still fail the
existing complete artifact/sender fingerprint checks.

Freshness is tied to the original local source observation. Relay proof expires
at the oldest required artifact observation or newest matching sender observation,
whichever expires first. Re-selecting retained/cached rows cannot slide that
deadline forward. Long-running collections reacquire expired source. Metadata
persistence receives the original epoch observation, validated separately from
monotonic proof deadlines; future/invalid timestamps cannot install fresh proof.

The unchanged per-window script count, per-script byte, aggregate source byte,
proof, URL and time budgets also apply to cached evidence. A cache hit consumes
its original decoded source size from the window budget, even though it avoids
a download. `source_budget_bytes` includes conservative failure charges and is
**not** network traffic. Logs add cache hit/miss counts; per-operation HTTP body
measurements remain the separate transport metric.

Regression fixtures cover window overlap, profile/actor/proxy isolation,
observation expiry, metadata freshness, cancellation/failure, changed source,
strict budgets, current-document authentication and cache bounds. They do not
establish live p50/p95, complete-account traffic or a promised percentage saving.
