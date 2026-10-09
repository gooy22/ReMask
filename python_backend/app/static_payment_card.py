"""Observed card screen and Save response; no executable Save/PTT contract yet.

The supplied Save sender establishes the persisted operation and envelope,
but delegates the entire mutation input to a missing tokenization builder.
Never turn this evidence into an invented card submission request.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path
import re

from .static_payment_read import ENDPOINT, _brand, _clean_payload, _identity, _node_id

MANIFEST = Path(__file__).with_name('contracts') / 'meta_payment_card_20261009.json'


@lru_cache(maxsize=1)
def _manifest():
    value = json.loads(MANIFEST.read_text())
    if (value.get('version') != 1
            or value.get('scope') != 'observed_save_envelope_no_tokenization_no_submission'):
        raise ValueError('PAYMENT_CARD_CONTRACT_INVALID')
    for name, kind in (('READ_CARD_SCREEN', 'query'), ('SAVE_CARD', 'mutation')):
        row = value['operations'][name]
        if (row.get('operation_kind') != kind or row.get('endpoint_url') != ENDPOINT
                or not re.fullmatch(r'\d{5,40}', row.get('doc_id', ''))):
            raise ValueError('PAYMENT_CARD_CONTRACT_INVALID')
    # A manifest edit alone must never enable an incomplete implementation.
    save = value['operations']['SAVE_CARD']
    if any(save.get(k) is not False for k in
           ('input_schema_verified', 'tokenization_verified', 'execution_enabled', 'allow_token_proxy_fallback')):
        raise ValueError('PAYMENT_CARD_CONTRACT_INVALID')
    return value


def card_screen_command(payment):
    row = copy.deepcopy(_manifest()['operations']['READ_CARD_SCREEN'])
    row['variables']['paymentAccountID'] = _identity(payment)
    # country/currency/intent are observed optional null defaults. The form's
    # usability intent enum is not the Save builder's ADD_PM payment intent.
    return row


async def read_card_screen(web, *, business_id, payment):
    business_id = _identity(business_id)
    row = card_screen_command(payment)
    web.private_only = True
    return await web.graphql(row['doc_id'], row['variables'], friendly_name=row['friendly_name'],
                             endpoint_url=row['endpoint_url'], business_context_id=business_id)


def submission_contract_status():
    row = _manifest()['operations']['SAVE_CARD']
    return {'code': 'CARD_PRIVATE_INPUT_CONTRACT_UNAVAILABLE', 'save_operation_verified': True,
            'input_schema_verified': False, 'tokenization_verified': False,
            'execution_enabled': False, 'submitted': False, 'browser_started': False,
            'missing_source_modules': list(row['missing_source_modules'])}


def _payment_scope(payment, account, evidence):
    node = payment.get('billable_account') if isinstance(payment, dict) else None
    return (isinstance(evidence, dict) and evidence.get('account_scope_verified') is True
            and evidence.get('account_id') == account
            and _node_id(evidence.get('payment_account_node_id'))
            and isinstance(payment, dict) and payment.get('id') == evidence['payment_account_node_id']
            and isinstance(node, dict) and node.get('__typename') == 'AdAccount'
            and isinstance(node.get('id'), str) and node['id'].removeprefix('act_') == account)


def card_screen_proof(payload, account, *, account_evidence):
    account = _identity(account)
    base = {'account_id': account, 'account_scope_verified': False, 'card_form_verified': False,
            'submitted': False, 'browser_started': False, 'execution_enabled': False}
    if not _clean_payload(payload):
        return {**base, 'code': 'CARD_SCREEN_QUERY_REJECTED'}
    data = payload.get('data')
    payment = data.get('payment_account') if isinstance(data, dict) else None
    # This observed query does not request payment_legacy_account_id. Match
    # its Relay node and exact RK against an independent READ_ACCOUNT proof.
    if not _payment_scope(payment, account, account_evidence):
        return {**base, 'code': 'CARD_SCREEN_SCOPE_UNVERIFIED'}
    # Scope can be established even when the account currently has no card
    # option. Maintenance may use its public loader maps; Save still cannot.
    base = {**base, 'account_scope_verified': True}
    options = payment.get('billing_payment_method_options')
    if not isinstance(options, list) or len(options) > 100 or any(not isinstance(o, dict) for o in options):
        return {**base, 'code': 'CARD_SCREEN_OPTIONS_INCONCLUSIVE'}
    cards = [o for o in options if o.get('__typename') == 'AdAccountNewCreditCardOption']
    if len(cards) != 1:
        return {**base, 'code': 'CARD_SCREEN_OPTIONS_INCONCLUSIVE'}
    option = cards[0]
    names = ('check_make_default', 'can_save_to_business', 'verify_tokenization_required')
    if any(type(option.get(k)) is not bool for k in names):
        return {**base, 'code': 'CARD_SCREEN_OPTIONS_INCONCLUSIVE'}
    result = {**base, 'code': 'CARD_SCREEN_CONFIRMED', 'account_scope_verified': True,
              'card_form_verified': True, 'options': {k: option[k] for k in names}}
    safe = payment['billable_account'].get('billing_safe_mode_state')
    if isinstance(safe, dict) and safe.get('status') in {'PLACED', 'ENROLLED'}:
        result['bank_verification_required'] = True
    return result


def save_response_proof(payload, account, *, account_evidence, expected_card):
    """Interpret a response to an already submitted Save, never resubmit.

    SUCCESS confirms the Save response only; a separate scoped methods read
    must confirm this exact credential before durable linkage is committed.
    Keep bank-confirmation secrets out of diagnostics and job state.
    """
    account = _identity(account)
    base = {'account_id': account, 'submitted': True, 'retry_blocked': True,
            'funding_verified': False, 'account_scope_verified': False,
            'status': 'SUBMITTED_UNVERIFIED', 'code': 'CARD_SAVE_RESULT_UNKNOWN'}
    if not _clean_payload(payload):
        return base
    data = payload.get('data')
    root = data.get('xfb_billing_save_card_credential') if isinstance(data, dict) else None
    if not isinstance(root, dict):
        return base
    # Saving to a business and linking to an RK are distinct. Do not infer
    # RK linkage from a returned business payment account.
    scoped = any(_payment_scope(root.get(k), account, account_evidence)
                 for k in ('payment_account', 'linked_payment_account'))
    if not scoped:
        return {**base, 'code': 'CARD_SAVE_SCOPE_UNVERIFIED'}
    card = root.get('credit_card')
    if not isinstance(card, dict) or card.get('__typename') != 'ExternalCreditCard':
        return base
    brand, last4 = _brand(card.get('card_association_name')), card.get('last_four_digits')
    if (not _node_id(card.get('id')) or not _node_id(card.get('credential_id'))
            or not brand or not isinstance(last4, str) or not re.fullmatch(r'\d{4}', last4)):
        return base
    if (not isinstance(expected_card, dict) or expected_card.get('type') != brand
            or expected_card.get('last4') != last4):
        return {**base, 'code': 'CARD_SAVE_CREDENTIAL_MISMATCH'}
    safe = {**base, 'account_scope_verified': True,
            'payment_account_id': account_evidence.get('payment_account_id'),
            'payment_account_node_id': account_evidence['payment_account_node_id'],
            'credential': {'id': card['id'], 'credential_id': card['credential_id'],
                           'type': brand, 'last4': last4}}
    status = root.get('card_verification_status')
    if status == 'AUTHENTICATION_REQUIRED':
        return {**safe, 'status': 'ACTION_REQUIRED', 'code': 'CARD_BANK_CONFIRMATION_REQUIRED'}
    if status == 'SUCCESS':
        return {**safe, 'status': 'VERIFYING', 'code': 'CARD_SAVE_REQUIRES_LINK_VERIFICATION'}
    return safe


def confirm_saved_card(saved, methods, *, business_id):
    """Commit candidate only after an independent exact RK/BM/credential read."""
    business_id = _identity(business_id)
    if not isinstance(saved, dict):
        raise ValueError('INVALID_CARD_SAVE_PROOF')
    if saved.get('status') != 'VERIFYING':
        return copy.deepcopy(saved)
    card = saved.get('credential')
    if (not isinstance(card, dict) or not isinstance(methods, dict)
            or saved.get('account_scope_verified') is not True
            or methods.get('account_id') != saved.get('account_id')
            or methods.get('business_id') != business_id
            or methods.get('payment_account_id') != saved.get('payment_account_id')
            or any(methods.get(k) is not True for k in ('account_scope_verified', 'business_scope_verified',
                    'payment_account_relation_verified', 'methods_query_verified'))):
        return copy.deepcopy(saved)
    rows = methods.get('payment_methods')
    if not isinstance(rows, list):
        return copy.deepcopy(saved)
    matches = [row for row in rows if isinstance(row, dict) and row.get('credential_id') == card['id']]
    if (len(matches) != 1 or matches[0].get('type') != card.get('type')
            or matches[0].get('last4') != card.get('last4') or matches[0].get('linkage_status') != 'OBSERVED'):
        return copy.deepcopy(saved)
    return {**copy.deepcopy(saved), 'status': 'LINKED', 'code': 'CARD_LINK_CONFIRMED',
            'business_id': business_id, 'business_scope_verified': True,
            'payment_account_relation_verified': True, 'card_linked': True}
