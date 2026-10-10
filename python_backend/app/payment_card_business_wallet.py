"""Read-only diagnosis for Meta's separate business-level card wallet.

The observed BillingHubPaymentMethodsViewQuery (SHA-256
b03371c15d173522abf4e8510f297c8b2502f36323710bd1af8a519c88535beb)
lists business payment-account credentials. It never attaches or saves cards.
A wallet credential is NOT evidence of attachment to an advertising account.
"""
from __future__ import annotations

import asyncio

from .static_payment_read import ENDPOINT, _brand, _clean_payload, _identity, _node_id

DOC_ID = '28635882856071901'
FRIENDLY_NAME = 'BillingHubPaymentMethodsViewQuery'


def business_wallet_card_stage(payload, *, account, business_id, saved):
    """Return a bounded diagnostic only, with no credential IDs or card fields."""
    account, business_id = _identity(account), _identity(business_id)
    card = saved.get('credential') if isinstance(saved, dict) else None
    if not isinstance(card, dict) or not _node_id(card.get('id')):
        return 'business_wallet_save_identity_missing'
    data = payload.get('data') if isinstance(payload, dict) else None
    business = data.get('business') if isinstance(data, dict) else None
    if (not _clean_payload(payload) or not isinstance(business, dict)
            or str(business.get('id')) != business_id):
        return 'business_wallet_scope_unverified'
    payment = business.get('billing_payment_account')
    if not isinstance(payment, dict) or not _node_id(payment.get('id')):
        return 'business_wallet_payment_account_missing'
    if payment['id'] == saved.get('payment_account_node_id'):
        return 'business_wallet_same_as_rk'
    rows = payment.get('billing_payment_methods')
    if not isinstance(rows, list) or len(rows) > 500:
        return 'business_wallet_methods_unavailable'
    # Use only exact credential IDs returned by the *previous* Meta Save.
    ids = {card.get(k) for k in ('id', 'credential_id')
           if _node_id(card.get(k))}
    matches = []
    for row in rows:
        credential = row.get('credential') if isinstance(row, dict) else None
        if not isinstance(credential, dict):
            return 'business_wallet_methods_unavailable'
        candidate_ids = {credential.get(k) for k in ('id', 'credential_id')
                         if _node_id(credential.get(k))}
        if ids.isdisjoint(candidate_ids):
            continue
        matches.append(credential)
    if len(matches) > 1:
        return 'business_wallet_card_ambiguous'
    if not matches:
        # Presence is proven here, but absence isn't: Meta may filter the list.
        return 'business_wallet_card_not_observed'
    candidate = matches[0]
    if (candidate.get('__typename') != 'ExternalCreditCard'
            or _brand(candidate.get('card_association_name')) != card.get('type')
            or candidate.get('last_four_digits') != card.get('last4')):
        return 'business_wallet_card_metadata_unverified'
    connected = candidate.get('linked_ad_accounts')
    nodes = connected.get('nodes') if isinstance(connected, dict) else None
    if isinstance(nodes, list) and any(
            isinstance(node, dict) and str(node.get('id', '')).removeprefix('act_') == account
            for node in nodes):
        # Even if the Business Hub claims linkage, the RK's independently
        # scoped payment methods are still authoritative for LINKED status.
        return 'business_wallet_reports_rk_link_but_rk_methods_missing'
    return 'business_wallet_card_saved_not_attached_to_rk'


async def inspect_business_wallet_card(web, *, account, business_id, saved):
    """Query owner Business wallet, never POST card or confirm a financial action."""
    account, business_id = _identity(account), _identity(business_id)
    variables = {
        'assetID': account,
        'businessID': business_id,
        'paymentAccountID': '',
        'preloadPaymentAccount': True,
        'billable_account_types': ['FB_ADS'],
        'connected_asset_limit': 10,
        'connected_asset_detail_limit': 10,
        'include_billable_accounts_with_credentials': True,
        'only_show_account_info_tooltip': False,
    }
    try:
        async with asyncio.timeout(18):
            web.private_only = True
            response = await web.graphql(DOC_ID, variables, friendly_name=FRIENDLY_NAME,
                                         endpoint_url=ENDPOINT, business_context_id=business_id)
        return business_wallet_card_stage(response, account=account,
                                          business_id=business_id, saved=saved)
    except Exception:
        return 'business_wallet_read_unavailable'
