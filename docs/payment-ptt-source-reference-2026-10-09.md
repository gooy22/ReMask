# Payment Save input / PTT source candidates (2026-10-09)

## Located public module definitions

These are third-party archived public JS modules, **not** a verified Meta Business billing capture. Do not install or execute them as the production card-saver without compatibility and authorized live validation.

- `BillingCreditCardUtils`: https://github.com/vinikjkkj/wa-diff/blob/d712847306f96a2b72b5b28836dca22829b09437/files/BillingCreditCardUtils.js
  - Git blob SHA: `4cbfb0a7451dd45cbb887066aba79e8de5091e58`
  - Defines `buildSaveCardCredentialInput`; returns an envelope including `billing_address`, `card_data`, `payment_account_id`, `payment_intent`, `platform_trust_token`, `currency`, `client_info`, `set_default`, and consent/availability fields.
  - The `card_data` object includes encrypted/protected card-number and security-code string fields. No real card data belongs in fixtures, logs, exports or commits.
- `getPTTUtils`: https://github.com/vinikjkkj/wa-diff/blob/d712847306f96a2b72b5b28836dca22829b09437/files/getPTTUtils.js
  - Git blob SHA: `ee7a7af91e1aaa86894bf87dc0945a1c3cdb16a3`
  - Exports `getPTTInternalWithEncryption` and `getPTTInternal`. This is an implementation, not just a reference to the name.
- Related archive: `XPlatReactCrypto`, `FBPayAuthLibraryCommon`, `FBPayCometBase64URL`, `BillingCreditCardNumber` also exist in the same repository.

## Compatibility boundary

Current ReMask fixture `meta_payment_save_observed_20261009.js` confirms the Save sender calls `BillingCreditCardUtils.buildSaveCardCredentialInput` with 15 parameters and passes its result to `BillingSaveCardCredentialStateMutation` as `input`; the archived builder accepts 15 parameters. This is structural agreement, **not** live verification.

The `modularGeneratePTT` definition from the operator's billing export includes a `paymentProductID` option/extra argument that is absent from the archived `modularGeneratePTT` definition. Accordingly, the archived `getPTTUtils` may be an incompatible revision. `BillingPTTUtils` and `BillingPTTSharedUtils` are already present in the operator's export; use them as the current reference instead of substituting archive versions.

The existing manifest intentionally retains `input_schema_verified=false`, `tokenization_verified=false`, `execution_enabled=false`. Do not flip these flags or send a card mutation on the strength of this archive. Next: compare exact live JS signatures and validate token handling and isolated scope/response on authorized test accounts. Do not store PAN, CVV, cookies or live PTT in test fixtures.
