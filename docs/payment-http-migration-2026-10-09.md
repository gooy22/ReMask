# Payment HTTP migration evidence

The operator supplied a Safari PDF of the explicit maintenance export. Its 618
public module definitions were recovered from the PDF text objects. Every
reconstructed definition matches its exported SHA-256. Authenticated HTML,
cookies, PAN and CVV are not part of the source fixture.

## Confirmed read contracts

`python_backend/app/contracts/meta_payment_read_20261009.json` and
`app/static_payment_read.py` contain five query-only, versioned contracts:

| Operation | doc_id | Identity role |
| --- | --- | --- |
| BillingHubPaymentSettingsViewQuery | 28797973873175785 | Exact canonical RK → billing payment account |
| BillingPaymentMethodDisplayUtilsQuery | 27586872297608269 | Exact payment credential → brand and last4 |
| BillingHubPaymentSettingsPaymentMethodsListQuery | 28814526004898205 | Exact RK/BM/payment node → returned masked card credentials |
| BillingCountryCurrencyPageViewManagerQuery | 28388533884149241 | Exact payment-account/RK setup and allowed options, read-only |
| BillingSelectPaymentMethodPageViewManagerQuery | 29195809800004536 | Exact payment-account/RK payment options, read-only |

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
also cannot prove linkage to an RK.

The new operator JSON has 1,070 module definitions (1,913,263 source bytes),
no export truncation and no script errors. Every SHA-256 matches. It supplies
the full methods, setup and options query artifacts, but still no card-save
artifact/sender or tokenization implementation. Its live READ_ACCOUNT probe
confirmed RK 120251650486340295 → payment account 1483335817184793.
Twelve curated modules are retained in `meta_payment_methods_observed_20261009.js`;
IDs, kinds, variables and hashes are tested against the pinned catalog.

The public payment-method inspection and Cards reconciliation now use only the
static HTTP reader. They never create a browser or fall back to one on errors.
Positive linkage requires the exact canonical RK, `owning_business.id`, and
payment node identity from an independent READ_ACCOUNT query. Returned cards
are sanitized to credential ID, brand, last4 and explicit boolean metadata.
Repeated aliases for the same credential are deduplicated; conflicting metadata
is inconclusive. Different credentials with the same mask stay distinct.
The PHP vault accepts native positive proofs only with all relation gates and
one matching observed credential. Filtered reads cannot erase a previously
confirmed link merely because the old card was not returned.

The methods query has PRIMARY_ONLY/allowlist filters. It proves returned
instruments, but empty arrays do not prove absence and never unlock a retry.
Inspection commits only a positively observed link; an inconclusive read
preserves the previous durable state. Neither an attached card nor an expired
or unverified credential implies bank verification or readiness to spend.
The existing card submission binder remains unchanged until tokenization,
setup mutation handling and ATTACH contracts are proven.

## Corrected maintenance capture

The first export hit the previous 1 MB source limit (999,919 exported bytes).
It also omitted resources loaded only when the payment wizard is opened. A
successful HTTP document read and `SOURCE_EVIDENCE` did not prove that the card
submission contract was present.

Maintenance now resolves only literal `compMap`/`rsrcMap` Bootloader resources
observed in the billing document, verified options-query Relay extensions and
public JS loader maps. It fetches deferred payment JS before remaining eager
bundles, deduplicates URLs across recursive maps and bounds total reads to 128
scripts/40 MB. Script omissions are explicit.
It never guesses CDN addresses, executes JS, opens Chromium or submits a card.
Conflicting resource definitions and foreign/credential-bearing URLs are
rejected. Artifact definitions and card/tokenization senders have export
priority. The 8 MB export limit is separate from the bounded 40 MB source read
budget. Truncation, unread scripts and missing required source modules are
reported explicitly. The protected PHP export downloads an exact JSON file so
an operator need not print the source to PDF.

The export performs pinned READ_ACCOUNT, READ_METHODS and READ_OPTIONS probes
within a separate 20-second budget. Only sanitized identity/masked proofs and
loader-map counts are returned; authenticated query bodies are never exported.
A verified options response may provide observed public JS resource maps. Query
failure cannot prevent the subsequent public source capture or start Chromium
or a card mutation. Source reads have their own 65-second budget.

All private HTTPS Facebook origins use the existing HTTP/2-only transport,
profile cookies/proxy and zero transport retries. HTTPX library INFO logging is
disabled because it otherwise exposes full redirected session URLs; the shared
transport retains method/host/status/protocol diagnostics without URL queries.

## Remaining work and verification limits

The new snapshot still does not contain the
`BillingSaveCardCredentialStateMutation` definition or sender. An operation name in
`BillingTokenProxyFallbackPolicy` does not establish its request schema. The
observed policy explicitly disallows a Token Proxy fallback for that web card
operation; the actual tokenization implementation must be inspected rather than
replaced with an assumed legacy endpoint.

Full HTTP card binding still requires complete observed schema/response evidence
for card tokenization, ATTACH and setup mutation/result handling, followed
by exact instrument/RK verification and retained-intent reconciliation. No card
binding success, fresh methods-query success or complete deeper lazy-source
coverage has yet been established live for this revision. Unit fixture success
alone cannot certify those results.
