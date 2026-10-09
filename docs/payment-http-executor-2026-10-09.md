# Card HTTP implementation and verification boundary

## Implemented

`payment_ptt.py` ports the supplied current `FBPayAuthLibraryCommon` and
`FBPayAuthLibraryUtils` encryption branch to Python: ephemeral P-256 ECDH,
observed ConcatKDF, AES-256-GCM, six-part protected payload, and the outer PTT.
The observed Billing caller supplies no signing key; the resulting signatures
array is empty, matching the source rather than inventing a device key.
Certificate signatures, validity, CA/key usage and the pinned Payments Root CA
are checked. Feature flags that bypass trust validation are not ported.

`payment_card_input.py` implements the archived 15-parameter builder's exact-RK
ADD_PM subset. PAN/CVV remain in the encrypted secret payload; Save input uses
`$e2ee`. Business-wide sharing, CHARGE, CVV omission, plaintext fallback and
automatic consent are excluded. Undefined optional fields are omitted; observed
null fields retain null.

`payment_card_http.py` implements PRECHECK → KEY → SAVE → independent VERIFY.
All calls use fixed persisted operation IDs and the existing private HTTP
session. A required durable callback runs at the transport's before-submit
boundary. A lost Save reply never triggers another Save. A successful reply
must be followed by an exact returned credential/RK/BM/payment-account match.
Lost verification preserves the returned credential. Bank challenges stay
ACTION_REQUIRED, without exporting bank parameters. No funding claim is made.

The current `getBillingWizard3DSClientInfo` definition was also located in the
operator JSON. Its four-field schema is ported and compared with the original
function. The executor requires declared profile viewport/depth values; it does
not invent a browser measurement or treat null client info as verified.

The operator TXT also supplies `useBillingBinInfoQuery` (37633143606284498).
Before requesting a PTT key or sending Save, the executor now queries the exact
payment/country/currency/BIN tuple and checks card support, postal/contact fields
and recurring consent. The e-mandate rule matches the supplied
`BillingEMandateConsentUtils` across all prepaid/recurring/mandate branches.
Its CVV exemption flag never removes mandatory CVV from this implementation.

Country approval is no longer an internal manually enabled context flag.
After proving the screen's exact payment/RK scope, the executor reads its live
tax country, predicted country, billing flags and payment modes. The current
caller passes TAX_COUNTRY_MISMATCH as inCountrySpoofingExperiment. A confirmed
no-mismatch branch can proceed; mismatch or incomplete policy stops before
key/Save. The missing tax-validation contract is not bypassed.

The public `prepare` action now uses pinned account/methods/form reads and
returns FORM_CONFIRMED, which means form availability only, not Save readiness.

## Evidence and tests

- Current crypto modules: operator JSON, source SHA-256
  `7c8502d3f612ee6cc6af6cbd0524d667402bdd09346629f5effa76ec2a5d48a4`
  and `bbb76d00b393288223b6b253a5d3e6584f95cf188ffa795bc3ab8d0ad02c7318`.
- Builder and wrapper: `vinikjkkj/wa-diff` at
  `d712847306f96a2b72b5b28836dca22829b09437`, blobs
  `4cbfb0a7451dd45cbb887066aba79e8de5091e58` and
  `ee7a7af91e1aaa86894bf87dc0945a1c3cdb16a3`.
- Python PTT matches isolated original JavaScript byte-for-byte with synthetic
  keys, nonce, IV, PAN and CVV. Independent server-side decryption validates AAD
  and secret payload. Builder output matches the archived function.
- Tests cover foreign scope, runtime/context gates, key errors, durable-intent
  failures, lost submit replies, lost verification, and bank confirmation.
  JavaScript references run only in offline tests, never in production workers.

The newer modularGeneratePTT wrapper has an additional paymentProductID option.
The supplied current BillingPTTUtils caller does not pass this option, so the
extra argument is undefined for this observed path. This narrows the revision
concern but does not prove the current builder or live server accepts our Save.

## Remaining release gate

Public card `bind` still uses the legacy implementation. The new HTTP Save
executor is intentionally not wired to it. Its internal SaveContext defaults
to unverified; current builder compatibility, profile client-info values, consent state and
runtime fragment flag must be confirmed before an adapter can construct a
verified context. The existing manifest execution flags remain false.
Offline parity does not establish live card attachment.

The no-mismatch country branch and recurring-consent rule now have source and
fixture coverage. Accounts flagged for tax-country validation still need the
separate current contract. No live Save or payment was performed by these tests.

Latest uploaded `remask-payment-contracts (4)(5).json` still reports 128 scripts
read out of 387, 259 unread, 1134 definitions, and no current builder/getPTTUtils
definition. The replacement TXT is byte-identical to the preceding TXT. The
deployed collector has a 400-script limit, but no fresh 400-script capture is
established by these uploads. Do not silently discard pending card intents to
retry or report a completed migration based on this reference implementation.

Live read checked through the Cards UI for profile 15, RK 120251650486340295:
`PAYMENT_METHODS_FILTERED_NO_CARD`, browser_started=False, 2026-10-09 17:10:54 UTC.
This filtered result is not absence proof and not authorization to replay Save.
