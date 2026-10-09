# Card country validation and public reconciliation

The last production attempt stopped before PTT/Save on the local blanket
TAX_COUNTRY_MISMATCH gate. That flag activates a country-validation branch;
the supplied BillingSaveCardCredentialState source first reads its status.

The fresh profile-15 capture read 400 scripts and located current
BillingCountryVerificationUtils plus its two query artifacts. Production now
pins tax status query 24964251583237065 and BIN-country query
24756186460717402. The capture itself remains partial (369 scripts unread),
so this change makes no completeness claim about unrelated modules.

A CONFIRMED status can proceed with the existing exact-account and consent
checks. An unconfirmed status with can_update_tax_country=true requires the
observed six-digit BIN and freshly encrypted PTT query before Save. A missing
or different BIN country stops before Save. The implementation does not
change tax country, acknowledge a mismatch, or implement identity step-up.
When can_update_tax_country=false, an explicit step-up-required result remains.

Prepare reports the live country-policy stage without sending card fields.
The public operation allowlist also now includes reconcile; its previous
omission prevented the existing HTTP ledger from finishing a lost Save reply.
Regression tests cover public reconciliation without PAN/CVV or replay, query
artifact hashes/schema, foreign tax scope, and both permitted country branches.

Offline tests and deployment health do not establish live card attachment.
Live Save still requires the exact returned credential and a separate scoped
payment-methods read before LINKED is committed.

Live verification on 2026-10-09 22:18 UTC passed account, methods, form,
requirements, key retrieval and certificate-validated PTT encryption. Meta's
BIN-country query returned a country different from the account country;
the attempt stopped with CARD_BILLING_COUNTRY_MISMATCH and submitted=false.
No Save was sent. Only the two validated ISO country codes are returned and
persisted for the operator; card input, BIN and PTT remain request-local.

The supplied BillingVerifyCountryLocationMismatchState offers Verify Country
(SHOW_STEPUP_OPTIONS) or Change Business Location. ReMask reports that choice;
it does not fabricate hasAcknowledgedCountryMismatch or complete identity
verification. This live country mismatch is separate from the original
missing country-query implementation.

The exact live pair was US for the account and UA for the selected card.
The operator's explicit country preference (Ukraine, preserving Meta's country
only when change is locked) was not being applied by the HTTP service.
The service now checks the exact BM/RK/payment relation, current country
permissions and supported country options before applying the selected country
through current mutation 29520642304190454. It retains the existing currency
and timezone. It never invokes close/create-new or a country acknowledgement.
Separate account and setup reads must confirm the same RK/payment pair and
desired country before the card executor can continue. Query/selector failures
do not qualify as a locked-country fallback.
