# Native Ads Manager session renewal

Reviewed Meta public modules, October 15, 2026.

| Module | Public script SHA-256 | Module SHA-256 |
| --- | --- | --- |
| XAdsCMRenewAccessTokenController | 20faa0683f8a68a27cf32987fdc65cfde9b695a3b56dee4c4419f0db0d917f7c | 74ef22d44d083f1153ff3d6ac64e2acb7db4320330d1f7dd4bc5abbda2134201 |
| AdsMgmtNetworkRefreshToken | 20faa0683f8a68a27cf32987fdc65cfde9b695a3b56dee4c4419f0db0d917f7c | 4f80297189fa979b39756f0ecb08da83597ef0bf7486a1ffc66232aa42441f75 |
| WebApiApplication | 0e74cb8e83a621f733d5189dafb5c1c98ab638e997dd6158b5bb2441f0474733 | 3438709f97a0e1ffb8a521f2e02072849bab00beb993819cc0c295a50d4aa96c |

`AdsMgmtNetworkRefreshToken` sends one `AsyncRequest` to the controller URI with `client_id`. Its response payload contains `access_token`, `client_id` and `session_expiry_seconds_remaining`. It passes the returned token and client to `ApiClient.setAccessTokenForClientID`.

`XAdsCMRenewAccessTokenController` declares `/ads/manager/ajax/renew_access_token/`; its optional inputs are `privacy_mutation_token` and `client_id`. The observed caller supplies `client_id` only, plus the normal cookie-authenticated AsyncRequest CSRF envelope. `WebApiApplication` recognizes Ads Manager client `119211728144504`.

ReMask uses this refresh only when both reviewed session initializers are absent after a fresh, actor-confirmed Ads Manager document. It sends one request over the same profile HTTP/2 pool with the same cookies and CSRF actor. A rejected refresh is never replayed. A returned session is memory-only and must confirm the exact selected account and BM before any advertising sender is returned. Conflicting initialization, login/checkpoint gates, foreign origins, changed actor and changed transport remain rejected.
