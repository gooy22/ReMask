"""Pure Save input adapter for the observed exact-RK ADD_PM branch.

This reference does not enable a production contract or send a request.
Country/currency, consents and runtime fragment flag need explicit evidence.
No business-wide sharing, funding, CVV omission or token proxy fallback.
"""
from __future__ import annotations

import copy
from datetime import date
import re

from .static_payment_read import _identity


def build_client_info(*, color_depth, viewport_width, viewport_height):
    """Observed getBillingWizard3DSClientInfo schema; dimensions must come from the profile."""
    if (type(color_depth) is not int or color_depth not in (16, 24, 30, 32)
            or type(viewport_width) is not int or not 100 <= viewport_width <= 16384
            or type(viewport_height) is not int or not 100 <= viewport_height <= 16384):
        raise ValueError('CARD_CLIENT_CONTEXT_REQUIRED')
    return {'color_depth': str(color_depth), 'java_enabled': False,
            'screen_height': str(viewport_height), 'screen_width': str(viewport_width)}


def validate_client_info(value):
    if not isinstance(value, dict) or set(value) != {'color_depth','java_enabled','screen_height','screen_width'}:
        raise ValueError('CARD_CLIENT_CONTEXT_REQUIRED')
    try:
        expected=build_client_info(color_depth=int(value['color_depth']),
            viewport_height=int(value['screen_height']),viewport_width=int(value['screen_width']))
    except (ValueError,TypeError):
        raise ValueError('CARD_CLIENT_CONTEXT_REQUIRED') from None
    if value != expected or value['java_enabled'] is not False:
        raise ValueError('CARD_CLIENT_CONTEXT_REQUIRED')
    return expected


def card_auth_fields(values):
    number, cvv = values.get('number'), values.get('cvv')
    month, year = values.get('month'), values.get('year')
    if (not isinstance(number, str) or not re.fullmatch(r'\d{12,19}', number)
            or not isinstance(cvv, str) or not re.fullmatch(r'\d{3,4}', cvv)):
        raise ValueError('CARD_DATA_INVALID')
    try:
        month, year = int(month), int(year)
        if not 1 <= month <= 12 or not 2000 <= year <= 2099 or (year, month) < (date.today().year, date.today().month):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('CARD_DATA_INVALID') from None
    return ({'credit_card': '$e2ee', 'expiry_month': str(month), 'expiry_year': str(year), 'csc': '$e2ee'},
            {'credit_card': number, 'csc': cvv})


def build_save_input(values, *, payment, country, currency, token, client_info, logging_data,
                     usability_intent=None, network_consent=None, recurring_consent=None):
    auth, _ = card_auth_fields(values)
    if (not isinstance(token, str) or not token or len(token) > 65536
            or not re.fullmatch(r'[A-Za-z0-9_-]+', token)):
        raise ValueError('CARD_PTT_REQUIRED')
    if not isinstance(country, str) or not re.fullmatch(r'[A-Z]{2}', country):
        raise ValueError('CARD_BILLING_COUNTRY_REQUIRED')
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('CARD_BILLING_CURRENCY_REQUIRED')
    if not isinstance(logging_data, dict):
        raise ValueError('CARD_LOGGING_CONTEXT_REQUIRED')
    card = {'bin': values['number'][:8], 'cardholder_name': values.get('holder') or '',
            'credit_card_number': {'sensitive_string_value': '$e2ee'},
            'csc': {'sensitive_string_value': '$e2ee'}, 'expiry_month': auth['expiry_month'],
            'expiry_year': auth['expiry_year'], 'last_4': values['number'][-4:]}
    for source, target in (('email', 'cardholder_email'), ('phone', 'cardholder_phone_number')):
        if values.get(source):
            card[target] = values[source]
    address = {'country_code': country}
    if values.get('postal_code'):
        address['zip'] = values['postal_code']
    result = {'billing_address': address, 'card_data': card, 'client_info': copy.deepcopy(client_info),
              'currency': currency, 'is_hardware_backed_crypto_available': False,
              'payment_account_id': _identity(payment), 'payment_intent': 'ADD_PM',
              'platform_trust_token': token, 'set_default': False,
              'share_to_child_payment_account_id': None, 'skip_cvv_for_eea_save': False,
              'upl_logging_data': copy.deepcopy(logging_data)}
    if usability_intent is not None:
        # Caller must supply the observed enum KEY, not invent an enum value.
        if not isinstance(usability_intent, str) or not re.fullmatch(r'[A-Z][A-Z_]+', usability_intent):
            raise ValueError('CARD_USABILITY_INTENT_INVALID')
        result['intent'] = usability_intent
    for field, consent in (('network_tokenization_consent_given', network_consent),
                            ('recurring_payment_consent_given', recurring_consent)):
        if consent is not None:
            if type(consent) is not bool:
                raise ValueError('CARD_CONSENT_INVALID')
            result[field] = consent
    return result
