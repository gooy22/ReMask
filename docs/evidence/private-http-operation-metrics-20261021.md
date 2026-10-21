# Private HTTP operation measurements

`private_http_operation` logs aggregate existing `PrivateHttpClient` calls by
worker profile ID and a bounded operation name. They contain no request/response
content, URLs, query strings, headers, cookies, actor IDs or proxy credentials.

Coverage is deliberately explicit:

- Each runner task has its own scope (`private_launch`, `private_delivery`,
  `prepare`, `provisioning`, `proxy_check`, `transparent_post`; unknown actions
  use `other`). Resolver work before the runner task is outside that scope.
- Direct `profile_live_inventory` calls have a `sync_profile` scope covering the
  endpoint's wall time, including resolver/state overhead. A nested call for the
  same profile reuses an existing task scope, so its requests are counted once.
  The adapter preserves the endpoint's FastAPI path/query signature.
- Only `PrivateHttpClient` traffic contributes HTTP/body counters. Resolver
  `aiohttp` traffic, other direct API endpoints, uploads and the whole account
  lifecycle are not measured by these counters. Existing phase timings remain
  separate from the new transport observations.

## Counter meanings

| Field | Meaning |
| --- | --- |
| `request_scopes` / `completed_request_scopes` | Entered/completed private request context managers. |
| `request_attempts` | HTTPX request hook invocations, including followed redirects; an attempt does not prove delivery to Meta. |
| `responses` / `redirect_responses` | Observed response headers / observed redirect responses. |
| `encoded_response_body_bytes` | Sum of HTTPX `num_bytes_downloaded` after streaming: encoded response body bytes consumed from the stream. |
| `decoded_body_bytes_delivered` | Bytes actually returned by `_Body.read`, after decompression. Buffered bytes not returned after a failure or early close are excluded. |
| `decoded_redirect_body_bytes` | Decoded redirect bodies already consumed by HTTPX; never read again for metrics. |
| `operation_wall_ms` | Wall time of the scope; not the complete account lifecycle. |
| `request_scope_ms` | Sum of request context durations, including caller consumption time; concurrent scopes can overlap. |
| `status_classes`, `rate_limit_responses` | HTTP status classes and exact HTTP 429 responses. HTTP 200 does not establish Meta operation success. |
| `errors` | Safe exception classes leaving a request scope; `other` does not prove a transport failure. |

`complete:false` flags in-flight/detached requests, profile-scope mismatches,
measurement errors, unavailable partially consumed redirect bodies, or the
512-response reference budget. Such samples must not be reported as complete
operation traffic. Logging/measurement failure cannot change an action result.
`outcome:partial` on direct Sync means the endpoint returned `live_ready:false`;
HTTP success and inventory completeness remain separate results.

Encoded body bytes exclude HTTP headers, TLS, CONNECT/proxy framing, request
bodies and unconsumed transport buffers. They are **not proxy-billable bytes**.
No proxy charge or upload size is inferred from them. Preloaded synthetic HTTPX
responses have zero downloaded stream bytes; decoded fixture sizes are never
substituted for encoded downloads.

Local tests cover a real loopback HTTP gzip response, bounded/partial reads,
redirects and failures, deadlines, profile isolation, runner and direct Sync
scope reset, nested accounting and metric/log failures. These tests establish
counter semantics, not live Meta latency or traffic savings. p50/p95 and any
before/after improvement require comparable complete operation samples. A full
account-lifecycle or billable proxy baseline remains unmeasured.
