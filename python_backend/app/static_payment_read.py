"""Pinned billing reads; no JS discovery, browser, card fields or mutations.

This catalog proves scoped positive card linkage and reads setup/options.
Filtered empty collections do not prove absence. No ATTACH/TOKENIZE contract.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path
import re
import time

MANIFEST = Path(__file__).with_name('contracts') / 'meta_payment_read_20261009.json'
ENDPOINT = 'https://business.facebook.com/api/graphql/'


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{5,30}', value):
        raise ValueError('INVALID_PAYMENT_TARGET')
    return value


@lru_cache(maxsize=1)
def _manifest():
    value = json.loads(MANIFEST.read_text())
    if value.get('version') != 1 or value.get('scope') != 'read_only_no_card_submission':
        raise ValueError('PAYMENT_READ_CONTRACT_INVALID')
    for row in value['operations'].values():
        if (row['operation_kind'] != 'query' or row['endpoint_url'] != ENDPOINT
                or not re.fullmatch(r'\d{5,40}', row['doc_id'])):
            raise ValueError('PAYMENT_READ_CONTRACT_INVALID')
    return value


def command(operation, *, account='', credential='', payment='', now=None):
    # The immutable catalog contains queries only. No dynamic doc_id fallback.
    row = copy.deepcopy(_manifest()['operations'][operation])
    day = int(time.time() if now is None else now) // 86400 * 86400
    values = {'transactions_start': day - 14 * 86400, 'summary_end': day,
              'summary_start': day - 7 * 86400, 'pending_start': day - 30 * 86400}
    supplied = {'account': account, 'credential': credential, 'payment': payment}
    for variable in row['variables'].values():
        if isinstance(variable, str) and variable.startswith('$') and variable[1:] in supplied:
            key = variable[1:]
            if key == 'credential':
                if not _node_id(supplied[key]):
                    raise ValueError('INVALID_PAYMENT_TARGET')
                values[key] = supplied[key]
            else:
                values[key] = _identity(supplied[key])
    row['variables'] = {k: values[v[1:]] if isinstance(v, str) and v.startswith('$') else v
                        for k, v in row['variables'].items()}
    return row


async def execute(web, operation, *, business_id, **bindings):
    business_id = _identity(business_id)
    row = command(operation, **bindings)
    web.private_only = True
    return await web.graphql(row['doc_id'], row['variables'], friendly_name=row['friendly_name'],
                             endpoint_url=row['endpoint_url'], business_context_id=business_id)


def account_proof(payload, target):
    target = _identity(target)
    base = {'account_id': target, 'account_scope_verified': False, 'verification_status': 'UNVERIFIED',
            'payment_methods': [], 'card_linked': None, 'funding_verified': False,
            'source': 'private_facebook_billing_static_read', 'checked_live': True,
            'inventory_complete': False}
    if not _clean_payload(payload):
        return {**base, 'code': 'PAYMENT_ACCOUNT_QUERY_REJECTED'}
    data = payload.get('data')
    node = data.get('billable_account_by_asset_id') if isinstance(data, dict) else None
    if (not isinstance(node, dict) or node.get('__typename') != 'AdAccount'
            or not isinstance(node.get('id'), str) or node['id'].removeprefix('act_') != target):
        return {**base, 'code': 'PAYMENT_ACCOUNT_RELATION_UNVERIFIED'}
    payment = node.get('billing_payment_account')
    identity = payment.get('payment_legacy_account_id') if isinstance(payment, dict) else None
    if not isinstance(identity, str) or not re.fullmatch(r'\d{5,30}', identity):
        return {**base, 'code': 'PAYMENT_ACCOUNT_ID_UNVERIFIED'}
    # Account identity is confirmed. No card absence/linkage is inferred from
    # a query that does not fetch the complete methods collection.
    proof = {**base, 'account_scope_verified': True, 'payment_account_id': identity,
             'code': 'PAYMENT_ACCOUNT_CONFIRMED'}
    if _node_id(payment.get('id')):
        proof['payment_account_node_id'] = payment['id']
    return proof


def _node_id(value):
    # Relay node IDs can be opaque. They are identity data, never card fields.
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_:+/=.-]{1,200}', value))


def _clean_payload(payload):
    return (isinstance(payload, dict) and not payload.get('errors') and not payload.get('error')
            and payload.get('hasNext') is not True
            and not (isinstance(payload.get('extensions'), dict)
                     and payload['extensions'].get('is_final') is False))


def _brand(value):
    if not isinstance(value, str):
        return None
    return {'visa': 'Visa', 'mastercard': 'Mastercard', 'americanexpress': 'Amex',
            'amex': 'Amex', 'discover': 'Discover'}.get(re.sub(r'[^a-z]', '', value.lower()))


def methods_proof(payload, target, *, business_id, account_evidence):
    target, business_id = _identity(target), _identity(business_id)
    base = {'account_id': target, 'business_id': business_id, 'account_scope_verified': False,
            'business_scope_verified': False, 'payment_account_relation_verified': False,
            'methods_query_verified': False, 'inventory_complete': False,
            'verification_status': 'UNVERIFIED', 'payment_methods': [], 'card_linked': None,
            'funding_verified': False, 'checked_live': True, 'browser_started': False,
            'source': 'private_facebook_billing_static_methods'}
    if not _clean_payload(payload):
        return {**base, 'code': 'PAYMENT_METHODS_QUERY_REJECTED'}
    data = payload.get('data')
    node = data.get('billable_account_by_asset_id') if isinstance(data, dict) else None
    owner = node.get('owning_business') if isinstance(node, dict) else None
    if (not isinstance(node, dict) or node.get('__typename') != 'AdAccount'
            or not isinstance(node.get('id'), str) or node['id'].removeprefix('act_') != target
            or not isinstance(owner, dict) or owner.get('id') != business_id):
        return {**base, 'code': 'PAYMENT_METHODS_SCOPE_UNVERIFIED'}
    payment = node.get('billing_payment_account')
    evidence = account_evidence if isinstance(account_evidence, dict) else {}
    if (evidence.get('account_id') != target or evidence.get('account_scope_verified') is not True
            or not _node_id(evidence.get('payment_account_node_id'))
            or not isinstance(payment, dict) or payment.get('id') != evidence['payment_account_node_id']):
        return {**base, 'code': 'PAYMENT_ACCOUNT_RELATION_UNVERIFIED'}
    collections = [payment.get(key) for key in
                   ('primary', 'billing_payment_methods_allowlist_customized', 'primary_funding_source_customized')]
    if any(not isinstance(rows, list) or len(rows) > 500 for rows in collections):
        return {**base, 'code': 'PAYMENT_METHODS_RESPONSE_INCOMPLETE'}
    cards = {}
    for rows in collections[1:]:
        for row in rows:
            credential = row.get('credential') if isinstance(row, dict) else None
            if (not isinstance(credential, dict) or not _node_id(credential.get('id'))
                    or not isinstance(credential.get('__typename'), str)):
                return {**base, 'code': 'PAYMENT_METHODS_RESPONSE_INCOMPLETE'}
            if credential['__typename'] != 'ExternalCreditCard':
                continue
            brand, last4 = _brand(credential.get('card_association_name')), credential.get('last_four_digits')
            if not brand or not isinstance(last4, str) or not re.fullmatch(r'\d{4}', last4):
                return {**base, 'code': 'PAYMENT_METHODS_CARD_METADATA_UNVERIFIED'}
            card = {'credential_id': credential['id'], 'type': brand, 'last4': last4,
                    'linkage_status': 'OBSERVED'}
            for key in ('is_expired', 'needs_verification', 'supports_recurring'):
                if type(credential.get(key)) is bool:
                    card[key] = credential[key]
            old = cards.get(card['credential_id'])
            if old is not None and old != card:
                return {**base, 'code': 'PAYMENT_METHODS_CREDENTIAL_CONFLICT'}
            cards[card['credential_id']] = card
    for row in collections[0]:
        credential = row.get('credential') if isinstance(row, dict) else None
        if (not isinstance(credential, dict) or not _node_id(credential.get('id'))
                or not isinstance(credential.get('__typename'), str)
                or (credential['__typename'] == 'ExternalCreditCard' and credential['id'] not in cards)):
            return {**base, 'code': 'PAYMENT_METHODS_RESPONSE_INCOMPLETE'}
    # PRIMARY_ONLY and an explicit allowlist are filtered collections. They
    # prove returned instruments, but empty results cannot authorize resubmit.
    proof = {**base, 'account_scope_verified': True, 'business_scope_verified': True,
             'payment_account_relation_verified': True, 'methods_query_verified': True,
             'payment_methods': list(cards.values()), 'payment_account_id': evidence['payment_account_id'],
             'code': 'PAYMENT_METHODS_OBSERVED' if cards else 'PAYMENT_METHODS_FILTERED_NO_CARD'}
    if cards:
        proof.update(verification_status='LINKED', card_linked=True)
    return proof


def payment_page_proof(payload, target, payment_id):
    """Prove only the exact payment-account/RK pair of setup/options queries."""
    target, payment_id = _identity(target), _identity(payment_id)
    if not _clean_payload(payload):
        return False
    data = payload.get('data')
    payment = data.get('payment_account') if isinstance(data, dict) else None
    node = payment.get('billable_account') if isinstance(payment, dict) else None
    return (isinstance(payment, dict) and payment.get('payment_legacy_account_id') == payment_id
            and isinstance(node, dict) and node.get('__typename') == 'AdAccount'
            and isinstance(node.get('id'), str) and node['id'].removeprefix('act_') == target)


async def inspect_methods(web, *, account, business_id):
    evidence = account_proof(await execute(web, 'READ_ACCOUNT', account=account, business_id=business_id), account)
    if evidence['account_scope_verified'] is not True:
        return {**evidence, 'source': 'private_facebook_billing_static_methods', 'browser_started': False}
    payload = await execute(web, 'READ_METHODS', account=account, payment=evidence['payment_account_id'],
                            business_id=business_id)
    return methods_proof(payload, account, business_id=business_id, account_evidence=evidence)


def credit_card_metadata(payload, target):
    if not _node_id(target):
        raise ValueError('INVALID_PAYMENT_TARGET')
    if not _clean_payload(payload):
        return None
    data = payload.get('data')
    node = data.get('node') if isinstance(data, dict) else None
    if not isinstance(node, dict) or node.get('id') != target or node.get('__typename') != 'ExternalCreditCard':
        return None
    last4, brand = node.get('last_four_digits'), node.get('card_association_name')
    if not isinstance(last4, str) or not re.fullmatch(r'\d{4}', last4) or not isinstance(brand, str) or not brand:
        return None
    brands = {'visa': 'Visa', 'mastercard': 'Mastercard', 'americanexpress': 'Amex', 'amex': 'Amex', 'discover': 'Discover'}
    normalized = brands.get(re.sub(r'[^a-z]', '', brand.lower()))
    if normalized is None:
        return None
    return {'id': target, 'type': normalized, 'last4': last4}


async def complete_methods(web, methods, *, business_id):
    """Expand the filtered UI list using the observed unfiltered query.

    The payment node must equal a fresh exact-RK account read. Credential
    metadata is read by the returned node IDs, never guessed from a mask.
    Missing/error responses cannot prove absence or authorize a replay.
    """
    if not all(methods.get(k) is True for k in ('account_scope_verified',
            'business_scope_verified', 'payment_account_relation_verified', 'methods_query_verified')):
        return methods
    account = methods['account_id']
    evidence = account_proof(await execute(web, 'READ_ACCOUNT', account=account,
                                         business_id=business_id), account)
    if (evidence.get('account_scope_verified') is not True
            or evidence.get('payment_account_id') != methods.get('payment_account_id')):
        return {**methods, 'inventory_complete': False, 'code': 'PAYMENT_ALL_METHODS_SCOPE_UNVERIFIED'}
    payload = await execute(web, 'READ_ALL_CREDENTIALS', payment=evidence['payment_account_id'],
                            business_id=business_id)
    payment = payload.get('data', {}).get('payment_account') if isinstance(payload.get('data'), dict) else None
    rows = payment.get('billing_payment_methods') if isinstance(payment, dict) else None
    if (not _clean_payload(payload) or not isinstance(payment, dict)
            or payment.get('id') != evidence.get('payment_account_node_id')
            or not isinstance(rows, list) or len(rows) > 500):
        return {**methods, 'inventory_complete': False, 'code': 'PAYMENT_ALL_METHODS_UNCONFIRMED'}
    credentials = {}
    for row in rows:
        card = row.get('credential') if isinstance(row, dict) else None
        if (not isinstance(card, dict) or not _node_id(card.get('id'))
                or not isinstance(card.get('__typename'), str) or card['id'] in credentials):
            return {**methods, 'inventory_complete': False, 'code': 'PAYMENT_ALL_METHODS_UNCONFIRMED'}
        credentials[card['id']] = card['__typename']
    known = {row['credential_id']: row for row in methods['payment_methods']}
    if any(credentials.get(key) != 'ExternalCreditCard' for key in known):
        return {**methods, 'inventory_complete': False, 'code': 'PAYMENT_METHODS_CREDENTIAL_CONFLICT'}
    for identity, typename in credentials.items():
        if typename != 'ExternalCreditCard' or identity in known:
            continue
        metadata = credit_card_metadata(await execute(web, 'READ_CREDENTIAL', credential=identity,
                                                     business_id=business_id), identity)
        if metadata is None:
            return {**methods, 'inventory_complete': False, 'code': 'PAYMENT_METHODS_CARD_METADATA_UNVERIFIED'}
        known[identity] = {'credential_id': identity, 'type': metadata['type'],
            'last4': metadata['last4'], 'linkage_status': 'OBSERVED', 'bank_verification_status': 'UNVERIFIED'}
    noncard_count = sum(kind != 'ExternalCreditCard' for kind in credentials.values())
    return {**methods, 'payment_methods': list(known.values()), 'inventory_complete': True,
        'all_credential_ids': list(credentials),
        'card_credential_count': sum(kind == 'ExternalCreditCard' for kind in credentials.values()),
        'non_card_credential_count': noncard_count,
        'verification_status': 'LINKED' if known else 'NONE' if not credentials else 'UNVERIFIED',
        'card_linked': bool(known), 'code': 'PAYMENT_ALL_METHODS_OBSERVED' if known
            else 'PAYMENT_ALL_METHODS_NON_CARD_ONLY' if noncard_count else 'PAYMENT_ALL_METHODS_NO_CARD'}
