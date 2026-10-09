# Payment HTTP migration evidence

The operator supplied a Safari PDF of the explicit maintenance export. Its 618
public module definitions were recovered from the PDF text objects. Every
reconstructed definition matches its exported SHA-256. Authenticated HTML,
cookies, PAN and CVV are not part of the source fixture.

## Confirmed read contracts

`python_backend/app/contracts/meta_payment_read_20261009.json` and
`app/static_payment_read.py` contain two query-only, versioned contracts:

| Operation | doc_id | Identity role |
| --- | --- | --- |
| BillingHubPaymentSettingsViewQuery | 28797973873175785 | Exact canonical RK → billing payment account |
| BillingPaymentMethodDisplayUtilsQuery | 27586872297608269 | Exact payment credential → brand and last4 |

The pinned query IDs, operation kinds, argument names and source hashes are
checked against seven observed modules in
`tests/fixtures/meta_payment_read_observed_20261009.js`. The first query follows
the observed sender's UTC date windows. Summary fetching is explicitly disabled
and the legacy display migration branch is selected; these are read options,
not evidence about which UI branch the current profile uses.

An error-free typed `AdAccount` with the exact canonical ID and an explicit
`billing_payment_account.payment_legacy_account_id` confirms the account
identity. It does not prove an empty methods inventory, card linkage, ownership
by a particular BM, bank verification or readiness to spend. Credential metadata
also cannot prove linkage to an RK. The existing binder remains unchanged until
complete methods inventory, setup, tokenization and ATTACH contracts are proven.

## Corrected maintenance capture

The first export hit the previous 1 MB source limit (999,919 exported bytes).
It also omitted resources loaded only when the payment wizard is opened. A
successful HTTP document read and `SOURCE_EVIDENCE` did not prove that the card
submission contract was present.

Maintenance now resolves only literal `compMap`/`rsrcMap` Bootloader resources
observed in the billing document and fetches relevant deferred payment JS first.
It never guesses CDN addresses, executes JS, opens Chromium or submits a card.
Conflicting resource definitions and foreign/credential-bearing URLs are
rejected. Artifact definitions and card/tokenization senders have export
priority. The 8 MB export limit is separate from the bounded 40 MB source read
budget. Truncation, unread scripts and missing required source modules are
reported explicitly. The protected PHP export downloads an exact JSON file so
an operator need not print the source to PDF.

The export also performs one independent pinned READ_ACCOUNT probe. Only its
sanitized identity proof is returned. Its timeout cannot discard source evidence
already collected. Probe failure never starts a browser or a card mutation.

All private HTTPS Facebook origins use the existing HTTP/2-only transport,
profile cookies/proxy and zero transport retries. HTTPX library INFO logging is
disabled because it otherwise exposes full redirected session URLs; the shared
transport retains method/host/status/protocol diagnostics without URL queries.

## Remaining work and verification limits

The supplied snapshot does not contain the complete methods-list query or
`BillingSaveCardCredentialStateMutation` definition. An operation name in
`BillingTokenProxyFallbackPolicy` does not establish its request schema. The
observed policy explicitly disallows a Token Proxy fallback for that web card
operation; the actual tokenization implementation must be inspected rather than
replaced with an assumed legacy endpoint.

Full HTTP card binding still requires complete observed schema/response evidence
for methods inventory, payment profile setup, tokenization and ATTACH, followed
by exact instrument/RK verification and retained-intent reconciliation. No card
binding success, fresh payment-account query success or complete lazy-source
coverage has yet been established live for this revision. Unit fixture success
alone cannot certify those results.
