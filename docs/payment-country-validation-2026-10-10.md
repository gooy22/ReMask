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
