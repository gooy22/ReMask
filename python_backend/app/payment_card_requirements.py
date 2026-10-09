"""Pinned, read-only card requirements from the operator's current JS.

No Save, consent synthesis, country change or CVV exemption is performed here.
BIN is sent only to the observed query; results and exceptions omit it.
"""
from __future__ import annotations

import re

from .static_payment_read import ENDPOINT, _clean_payload, _identity

BIN_DOC_ID = '37633143606284498'
BIN_FRIENDLY_NAME = 'useBillingBinInfoQuery'


def bin_command(*, payment, number, country, currency):
    if not isinstance(number, str) or not re.fullmatch(r'\d{12,19}', number):
        raise ValueError('CARD_DATA_INVALID')
    if not isinstance(country, str) or not re.fullmatch(r'[A-Z]{2}', country):
        raise ValueError('CARD_BILLING_COUNTRY_REQUIRED')
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('CARD_BILLING_CURRENCY_REQUIRED')
    # Current useBillingBinInfo takes getBin8(), falling back only when shorter.
    return {'paymentAccountID': _identity(payment), 'bin': number[:8],
            'country': country, 'currency': currency}


async def read_card_requirements(web, *, business_id, payment, number, country, currency):
    variables = bin_command(payment=payment, number=number, country=country, currency=currency)
    web.private_only = True
    return await web.graphql(BIN_DOC_ID, variables, friendly_name=BIN_FRIENDLY_NAME,
                             endpoint_url=ENDPOINT, business_context_id=_identity(business_id))


def country_policy_proof(payload, *, country):
    """Validate the no-mismatch branch observed in useBillingAddCreditCard.

The caller must independently prove this screen's payment/RK scope first.
TAX_COUNTRY_MISMATCH is passed as inCountrySpoofingExperiment to Save state.
If present, the missing tax-validation contract cannot be skipped.
"""
    base = {'country_policy_verified': False, 'payment_modes_verified': False}
    if not _clean_payload(payload):
        return {**base, 'code': 'CARD_COUNTRY_POLICY_INCONCLUSIVE'}
    payment = payload.get('data', {}).get('payment_account')
    account = payment.get('billable_account') if isinstance(payment, dict) else None
    if not isinstance(account, dict):
        return {**base, 'code': 'CARD_COUNTRY_POLICY_INCONCLUSIVE'}
    flags, tax, modes = account.get('billing_flags'), account.get('billable_account_tax_info'), account.get('payment_modes')
    if (not isinstance(flags, list) or len(flags) > 100 or any(not isinstance(x, str) for x in flags)
            or not isinstance(tax, dict) or type(tax.get('can_update_tax_country')) is not bool
            or not isinstance(modes, list) or len(modes) > 100 or any(not isinstance(x, str) for x in modes)):
        return {**base, 'code': 'CARD_COUNTRY_POLICY_INCONCLUSIVE'}
    if 'TAX_COUNTRY_MISMATCH' in flags:
        return {**base, 'code': 'CARD_TAX_COUNTRY_VALIDATION_REQUIRED'}
    actual, predicted = tax.get('business_country_code'), tax.get('predicated_business_country_code')
    if not isinstance(actual, str) or not re.fullmatch(r'[A-Z]{2}', actual):
        return {**base, 'code': 'CARD_COUNTRY_POLICY_INCONCLUSIVE'}
    if actual != country or predicted not in (None, '', actual):
        return {**base, 'code': 'CARD_BILLING_COUNTRY_MISMATCH'}
    if not {'SUPPORTS_PREPAY', 'SUPPORTS_POSTPAY'}.intersection(modes):
        return {**base, 'code': 'CARD_PAYMENT_MODE_INCONCLUSIVE'}
    return {**base, 'country_policy_verified': True, 'payment_modes_verified': True,
            'is_prepaid_only': 'SUPPORTS_PREPAY' in modes and 'SUPPORTS_POSTPAY' not in modes,
            'code': 'CARD_COUNTRY_POLICY_CONFIRMED'}


def requirements_proof(payload, *, values, is_prepaid_only, recurring_consent):
    """Return only operator-safe requirements, never BIN or raw response data."""
    base = {'card_requirements_verified': False, 'submitted': False, 'required_fields': []}
    if not _clean_payload(payload):
        return {**base, 'code': 'CARD_BIN_QUERY_REJECTED'}
    row = payload.get('data', {}).get('credit_card_bin_info_shim')
    flags = ('is_supported', 'request_postal_code', 'require_3ds', 'require_emandate',
             'require_phone_number_or_email', 'skip_cvv_for_eea_save', 'supports_recurring')
    if (not isinstance(row, dict) or any(k not in row or row[k] is not None and type(row[k]) is not bool for k in flags)
            or type(row.get('is_supported')) is not bool):
        return {**base, 'code': 'CARD_BIN_REQUIREMENTS_INCONCLUSIVE'}
    if row['is_supported'] is not True:
        return {**base, 'code': 'CARD_BIN_UNSUPPORTED'}
    required = []
    if not isinstance(values.get('holder'), str) or not values['holder'].strip():
        required.append('holder')
    if row['request_postal_code'] is True and not str(values.get('postal_code') or '').strip():
        required.append('postal_code')
    if row['require_phone_number_or_email'] is True and not any(
            isinstance(values.get(k), str) and values[k].strip() for k in ('email', 'phone')):
        required.append('email_or_phone')
    if required:
        return {**base, 'code': 'CARD_REQUIRED_FIELDS_MISSING', 'required_fields': required}
    # Exact BillingEMandateConsentUtils rule for ADD_PM (no Save-and-Pay).
    if type(is_prepaid_only) is not bool:
        return {**base, 'code': 'CARD_PAYMENT_MODE_INCONCLUSIVE'}
    if (row['require_emandate'] is True and row['supports_recurring'] is True
            and not is_prepaid_only and recurring_consent is not True):
        return {**base, 'code': 'CARD_RECURRING_CONSENT_REQUIRED'}
    return {**base, 'card_requirements_verified': True, 'code': 'CARD_BIN_REQUIREMENTS_CONFIRMED',
            'bank_verification_required': row['require_3ds'] is True}
