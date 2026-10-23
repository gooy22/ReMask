# ReMask latency baseline from existing logs (offline)

This tool is for **measuring general application performance**, not for querying Meta or issuing any operation. It consumes existing logs and produces a **numeric, aggregated report only**. Do not upload raw logs containing profile/session/proxy details to external services.

## Usage

From the `python_backend` directory:

```bash
python -m app.latency_report /path/to/local-combined.log > latency-summary.json
# Or, with a different slow-operation threshold:
python -m app.latency_report --slow-ms 10000 /path/to/local-combined.log
# The default threshold is 30000 ms.
```

The reader recognizes these existing formats:

- `private_http_operation {...}`: operation wall time and allowlisted phases (resolver, Ads Manager document, account proof, inventory, etc.).
- `worker_dispatch_latency item=... queue_age_ms=... profile_lock_ms=... resolver_ms=...`: queue, profile lock and resolver before a background task begins.
- `[ReMask sync latency] {...}`: safe numeric browser metrics for the entire selection.
- `[ReMask sync failure timing] {...}`: PHP timing after failed Sync transport.

It intentionally **does not join raw events** by user, profile, item ID, request ID or trace ID. Phase durations may overlap and must not be summed. Browser/UI metrics for a selection of several profiles are **not equivalent** to per-profile worker operations. If the browser loses an HTTP response, the report records a separate `incomplete_ui_http_records` count; observed UI wall time may not include the late backend result. The `low_sample_warning` is set for fewer than 20 samples, when p95 should be treated as especially uncertain. Values above one hour are omitted rather than silently distorting a latency analysis.

The report's `groups` show independent p50, p95, maximum, number and percentage at or above the slow threshold. Failed and partial outcomes are counted when an operation emitted them. All labels are fixed/validated, and untrusted JSON properties, profile names, IDs, URLs and secrets are never included in the report.

## How to interpret the next real benchmark

Measure the same normal, cold-cache, warm-cache and failed-request sequences on a deployed version **only when deployment is authorized**. Separate these four questions:

1. Is the 30-second pause **before** task execution? Inspect dispatcher queue age and profile-lock values.
2. Is it in internal setup? Inspect resolver, cache lookup and profile-session phases.
3. Is it in HTTP or account verification? Inspect Ads Manager document, account proof and inventory phases.
4. Does the backend finish faster than the operator sees it? Compare per-selection UI wall time, PHP bridge time, and UI apply/render time.

Keep separate baselines for single-profile Sync, multi-profile Sync, resource reads, and local post-job refresh. Compare p50/p95 and failure rates **before and after each change**, not just a fast test fixture. Running this report does not prove proxy-provider bytes, external Meta response latency, or real account-wide speedups without matching live observations.

Tests: `python -m unittest tests.test_latency_report` (also covered by backend discovery in GitHub Actions).
