"""Pinned, read-only card requirements from the operator's current JS.

No Save, consent synthesis, country change or CVV exemption is performed here.
BIN is sent only to the observed query; results and exceptions omit it.
"""
from __future__ import annotations

import re

from .static_payment_read import ENDPOINT, _clean_payload, _identity

BIN_DOC_ID = '37633143606284498'
BIN_FRIENDLY_NAME = 'useBillingBinInfoQuery'
TAX_DOC_ID = '24964251583237065'
COUNTRY_BIN_DOC_ID = '24756186460717402'


async def read_tax_country_validation(web, *, business_id, evidence):
    """Current BillingCountryVerificationUtils query with exact payment/RK proof."""
    web.private_only = True
    payload = await web.graphql(TAX_DOC_ID,
        {'paymentAccountID': _identity(evidence['payment_account_id'])},
        friendly_name='BillingCountryVerificationUtilsTaxCountryValidationDataQuery',
        endpoint_url=ENDPOINT, business_context_id=_identity(business_id))
    data = payload.get('data') if isinstance(payload, dict) else None
    payment = data.get('payment_account') if isinstance(data, dict) else None
    account = payment.get('billable_account') if isinstance(payment, dict) else None
    tax = account.get('billable_account_tax_info') if isinstance(account, dict) else None
    info = payment.get('tax_country_validation_info') if isinstance(payment, dict) else None
    if (not _clean_payload(payload) or evidence.get('account_scope_verified') is not True
            or not isinstance(payment, dict) or payment.get('id') != evidence.get('payment_account_node_id')
            or not isinstance(account, dict) or account.get('__typename') != 'AdAccount'
            or str(account.get('id', '')).removeprefix('act_') != evidence.get('account_id')
            or not isinstance(tax, dict) or type(tax.get('can_update_tax_country')) is not bool
            or 'tax_country_validation_info' not in payment
            or info is not None and (not isinstance(info, dict) or 'status' not in info
                or info['status'] is not None and not isinstance(info['status'], str))):
        return {'code': 'CARD_TAX_COUNTRY_QUERY_UNCONFIRMED', 'tax_status_confirmed': False}
    return {'code': 'CARD_TAX_COUNTRY_STATUS_READ',
            'tax_status_confirmed': isinstance(info, dict) and info['status'] == 'CONFIRMED',
            'can_update_tax_country': tax['can_update_tax_country']}


async def resolve_country_policy(web, payload, *, business_id, evidence, country):
    policy = country_policy_proof(payload, country=country)
    if policy['code'] != 'CARD_TAX_COUNTRY_VALIDATION_REQUIRED':
        return policy
    tax = await read_tax_country_validation(web, business_id=business_id, evidence=evidence)
    if tax['code'] != 'CARD_TAX_COUNTRY_STATUS_READ':
        return {**policy, 'code': tax['code']}
    if tax['tax_status_confirmed']:
        return {**policy, 'country_policy_verified': True, 'code': 'CARD_COUNTRY_POLICY_CONFIRMED'}
    if not tax['can_update_tax_country']:
        return {**policy, 'code': 'CARD_TAX_COUNTRY_STEPUP_REQUIRED'}
    # Current Save state checks BIN with the freshly built PTT before Save.
    # This is a pending read, not permission to change country or acknowledge mismatch.
    return {**policy, 'code': 'CARD_BIN_COUNTRY_CHECK_REQUIRED', 'bin_country_required': True}


async def confirm_bin_country(web, *, business_id, payment, number, token, country):
    """Query the observed six-digit BIN/PTT tuple; never export either."""
    if (not isinstance(number, str) or not re.fullmatch(r'\d{12,19}', number)
            or not isinstance(token, str) or not token):
        raise ValueError('CARD_DATA_INVALID')
    web.private_only = True
    payload = await web.graphql(COUNTRY_BIN_DOC_ID,
        {'bin': number[:6], 'paymentAccountID': _identity(payment), 'ptt': token},
        friendly_name='BillingCountryVerificationUtilsBinPropertiesQuery',
        endpoint_url=ENDPOINT, business_context_id=_identity(business_id))
    data = payload.get('data') if isinstance(payload, dict) else None
    row = data.get('credit_card_bin_properties') if isinstance(data, dict) else None
    detected = row.get('country_code') if isinstance(row, dict) else None
    if not _clean_payload(payload) or not isinstance(detected, str) or not re.fullmatch(r'[A-Z]{2}', detected):
        return {'country_policy_verified': False, 'code': 'CARD_BIN_COUNTRY_UNCONFIRMED'}
    return {'country_policy_verified': detected == country,
            'code': 'CARD_COUNTRY_POLICY_CONFIRMED' if detected == country else 'CARD_BILLING_COUNTRY_MISMATCH'}


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
    actual, predicted = tax.get('business_country_code'), tax.get('predicated_business_country_code')
    if not isinstance(actual, str) or not re.fullmatch(r'[A-Z]{2}', actual):
        return {**base, 'code': 'CARD_COUNTRY_POLICY_INCONCLUSIVE'}
    mismatch = 'TAX_COUNTRY_MISMATCH' in flags
    if actual != country or not mismatch and predicted not in (None, '', actual):
        return {**base, 'code': 'CARD_BILLING_COUNTRY_MISMATCH'}
    if not {'SUPPORTS_PREPAY', 'SUPPORTS_POSTPAY'}.intersection(modes):
        return {**base, 'code': 'CARD_PAYMENT_MODE_INCONCLUSIVE'}
    return {**base, 'country_policy_verified': not mismatch, 'payment_modes_verified': True,
            'is_prepaid_only': 'SUPPORTS_PREPAY' in modes and 'SUPPORTS_POSTPAY' not in modes,
            'code': 'CARD_TAX_COUNTRY_VALIDATION_REQUIRED' if mismatch else 'CARD_COUNTRY_POLICY_CONFIRMED'}


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
