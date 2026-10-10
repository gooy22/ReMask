"""HTTP-only Meta card-verification preflight for an exact saved card.

This deliberately does not claim to initiate a bank hold. Meta's verification
launcher opens a deferred wizard; its *financial mutation* is not in the
pinned contract catalog. A read-only query cannot initiate that mutation.
No Chromium, external Graph API, bank codes, CVV, or card submission here.
"""
from __future__ import annotations

import json
import re

from .payment_card_intents import CardIntentLedger
from .payment_inspection import account_id, resolve_payment_asset
from .private_auth import private_auth_error
from .static_payment_read import (
    account_proof, execute, methods_proof, payment_page_proof,
)


def _card_identity(payload):
    brand = str(payload.get('card_brand') or '').strip().lower()
    last4 = str(payload.get('card_last4') or '').strip()
    brand = {
        'americanexpress': 'Amex', 'amex': 'Amex',
        'mastercard': 'Mastercard', 'visa': 'Visa', 'discover': 'Discover',
    }.get(re.sub(r'[^a-z]', '', brand))
    if brand is None or re.fullmatch(r'\d{4}', last4) is None:
        return None
    return brand, last4


async def verify_payment_card_http(resolver, profile, payload, *, state=None):
    """Read exact scoped card/tasks over the profile's cookie/proxy HTTP session.

    A future pinned financial mutation may be dispatched ONLY after its
    request/response contract and once-only intent persistence are verified.
    Until then return a distinct NOT_DISPATCHED state, never success.
    """
    from .session import ProfileSession

    target = account_id(payload.get('account_id', ''))
    base = {'profile_id': profile, 'account_id': target, 'status': 'BLOCKED',
            'submitted': False, 'verification_triggered': False,
            'browser_started': False, 'funding_verified': False}
    card_id = payload.get('card_id')
    if not isinstance(card_id, str) or re.fullmatch(r'card_[a-f0-9]{24}', card_id) is None:
        return {**base, 'code': 'CARD_VERIFICATION_CARD_REQUIRED'}
    card = _card_identity(payload)
    if card is None:
        return {**base, 'code': 'CARD_VERIFICATION_CARD_REQUIRED'}
    try:
        asset = await resolve_payment_asset(profile, target, state, payload.get('asset_hint'))
        if not asset:
            return {**base, 'code': 'PAYMENT_ACCOUNT_BINDING_MISSING'}
        context = await resolver.resolve(profile)
        business = asset['business_id']
        if business == str(context.cookies.get('c_user') or ''):
            return {**base, 'code': 'PERSONAL_AD_ACCOUNT_EXCLUDED'}
        async with ProfileSession(context, timeout_seconds=30) as session:
            web = await session.facebook_web()
            evidence = account_proof(await execute(
                web, 'READ_ACCOUNT', account=target, business_id=business), target)
            if evidence.get('account_scope_verified') is not True:
                return {**base, 'code': evidence['code']}
            methods = methods_proof(await execute(
                web, 'READ_METHODS', account=target,
                payment=evidence['payment_account_id'],
                business_id=business), target,
                business_id=business, account_evidence=evidence)
            if not all(methods.get(field) is True for field in (
                'account_scope_verified', 'business_scope_verified',
                'payment_account_relation_verified', 'methods_query_verified')):
                return {**base, 'code': methods.get('code', 'PAYMENT_METHODS_SCOPE_UNVERIFIED')}

            # Match the actual credential ID from the retained Save whenever
            # possible. A masked brand/last4 is never enough to authorize money.
            saved_id = ''
            if state is not None and getattr(state, 'path', None):
                prior = await CardIntentLedger(state.path).source_read_intent(profile, target)
                if prior and prior.get('card_id') == card_id:
                    proof = json.loads(prior['result'])
                    credential = proof.get('credential') if isinstance(proof, dict) else None
                    if (isinstance(proof, dict) and proof.get('account_id') == target
                            and proof.get('business_id') == business
                            and proof.get('payment_account_id') == evidence['payment_account_id']
                            and isinstance(credential, dict)):
                        saved_id = str(credential.get('id') or credential.get('credential_id') or '')

            choices = [m for m in methods.get('payment_methods', ())
                       if m.get('type') == card[0] and m.get('last4') == card[1]
                       and (not saved_id or m.get('credential_id') == saved_id)]
            if len(choices) != 1:
                return {**base, 'code': 'CARD_VERIFICATION_CREDENTIAL_UNVERIFIED',
                        'funding': {**methods, 'profile_id': profile}}
            chosen = choices[0]
            selected = {'credential_id': chosen['credential_id'],
                        'card_confirmation_status': chosen.get('card_confirmation_status', 'UNKNOWN'),
                        'verification_tasks': chosen.get('verification_tasks', []),
                        'verification_tasks_observed': chosen.get('verification_tasks_observed') is True}
            common = {**base, **selected, 'card_linked': True,
                      'funding': {**methods, 'profile_id': profile}}
            if (chosen.get('needs_verification') is False
                    and chosen.get('verification_tasks_observed') is True
                    and not chosen.get('verification_tasks')):
                return {**common, 'status': 'OBSERVED',
                        'code': 'CARD_VERIFICATION_NO_REQUIRED_TASK'}

            options = await execute(web, 'READ_VERIFY_OPTIONS',
                                    payment=evidence['payment_account_id'], business_id=business)
            if not payment_page_proof(options, target, evidence['payment_account_id']):
                return {**common, 'code': 'CARD_VERIFICATION_OPTIONS_UNVERIFIED'}
            return {**common, 'status': 'ACTION_REQUIRED',
                    'code': 'CARD_VERIFICATION_MUTATION_NOT_PINNED',
                    'verification_stage': 'HTTP_VERIFICATION_TASK_OBSERVED_NOT_DISPATCHED'}
    except Exception as exc:
        auth = private_auth_error(exc)
        return {**base, 'code': auth.code if auth else
                ('PAYMENT_HTTP_TIMEOUT' if isinstance(exc, TimeoutError)
                 else 'CARD_VERIFICATION_HTTP_UNAVAILABLE')}
