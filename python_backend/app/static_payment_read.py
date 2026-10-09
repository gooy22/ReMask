"""Pinned billing reads; no JS discovery, browser, card fields or mutations.

This catalog proves the payment-account identity and masked credential metadata.
It does NOT yet contain a methods inventory or an ATTACH/TOKENIZE contract.
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


def command(operation, *, account='', credential='', now=None):
    # The immutable catalog contains queries only. No dynamic doc_id fallback.
    row = copy.deepcopy(_manifest()['operations'][operation])
    day = int(time.time() if now is None else now) // 86400 * 86400
    values = {'transactions_start': day - 14 * 86400, 'summary_end': day,
              'summary_start': day - 7 * 86400, 'pending_start': day - 30 * 86400}
    values['account' if operation == 'READ_ACCOUNT' else 'credential'] = _identity(
        account if operation == 'READ_ACCOUNT' else credential)
    row['variables'] = {k: values[v[1:]] if isinstance(v, str) and v.startswith('$') else v
                        for k, v in row['variables'].items()}
    return row


async def execute(web, operation, *, business_id, **bindings):
    business_id = _identity(business_id)
    row = command(operation, **bindings)
    return await web.graphql(row['doc_id'], row['variables'], friendly_name=row['friendly_name'],
                             endpoint_url=row['endpoint_url'], business_context_id=business_id)


def account_proof(payload, target):
    target = _identity(target)
    base = {'account_id': target, 'account_scope_verified': False, 'verification_status': 'UNVERIFIED',
            'payment_methods': [], 'card_linked': None, 'funding_verified': False,
            'source': 'private_facebook_billing_static_read', 'checked_live': True,
            'inventory_complete': False}
    if not isinstance(payload, dict) or payload.get('errors') or payload.get('error'):
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
    return {**base, 'account_scope_verified': True, 'payment_account_id': identity,
            'code': 'PAYMENT_ACCOUNT_CONFIRMED'}


def credit_card_metadata(payload, target):
    target = _identity(target)
    if not isinstance(payload, dict) or payload.get('errors') or payload.get('error'):
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
