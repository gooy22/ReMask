"""Cookie/proxy HTTP verification of an exact saved Meta payment card.

The SDC descriptor-authorisation and bank-code mutations are separate from
card Save and from any Add Funds flow. No browser, Graph API or Meta token.
"""
from __future__ import annotations

import json
import re

from .payment_card_intents import CardIntentLedger, BankVerificationLedger
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


def sdc_candidate_proof(payload, credential_id, brand, last4):
    """Check Meta's unverified-card screen without inferring status from empties.

    Never return a full credential, bank amount, or request token.
    The payment-account/BM linkage must already have been proven by the
    independent exact-RK account and methods reads.
    """
    base = {'sdc_screen_verified': False, 'sdc_candidate': False,
            'sdc_credential_match': False}
    if (not isinstance(payload, dict) or payload.get('errors') or payload.get('error')
            or payload.get('hasNext') is True):
        return base
    account = payload.get('data', {}).get('payment_account') if isinstance(payload.get('data'), dict) else None
    rows = account.get('billing_payment_methods') if isinstance(account, dict) else None
    if not isinstance(rows, list) or len(rows) > 500:
        return base
    found = []
    for row in rows:
        if not isinstance(row, dict):
            return base
        credential = row.get('credential')
        if not isinstance(credential, dict):
            return base
        if credential.get('__typename') != 'ExternalCreditCard':
            continue
        if str(credential.get('card_association_name') or '').strip().lower() == brand.lower() and credential.get('last_four_digits') == last4:
            found.append((row, credential))
    if len(found) > 1:
        return base
    if not found:
        return {**base, 'sdc_screen_verified': True}
    row, credential = found[0]
    exact = credential_id in (credential.get('id'), credential.get('credential_id'))
    # An SDC form may contain only a credential_id where the methods read
    # returned the Relay node id. In that case, never claim exact identity.
    return {**base, 'sdc_screen_verified': True, 'sdc_candidate': True,
            'sdc_credential_match': exact,
            'sdc_usability': str(row.get('usability') or '')[:48]}


SDC_SEND_DOC_ID = '29599506609664764'
SDC_VERIFY_CODE_DOC_ID = '28322139410820855'
BILLING_GRAPHQL = 'https://business.facebook.com/api/graphql/'


async def _send_sdc_once(web, *, state, profile, account, payment_account,
                         business, credential, card_id, common):
    """Execute only the observed Meta SFI descriptor initiation mutation.

    SQLite reservation is committed before any network call. No automatic
    repeat is allowed after an uncertain transport outcome or worker restart.
    """
    if state is None or not getattr(state, 'path', None):
        return {**common, 'code': 'SDC_DURABLE_GUARD_REQUIRED'}
    ledger = BankVerificationLedger(state.path)
    reserved = await ledger.reserve(profile=profile, account=account,
        payment_account=payment_account, credential=credential, card_id=card_id,
        flow='SDC')
    if not reserved['same_card']:
        return {**common, 'code': 'SDC_DIFFERENT_CARD_ATTEMPT_BLOCKED'}
    if not reserved['reserved']:
        return {**common, 'status': 'ACTION_REQUIRED',
                'code': 'SDC_AUTH_ALREADY_ATTEMPTED',
                'verification_stage': reserved['stage']}
    attempt = reserved['attempt_id']
    if not await ledger.mark(attempt, 'REQUEST_SENT'):
        return {**common, 'code': 'SDC_AUTH_RESERVATION_LOST'}
    # The mutation input was observed in Meta's BillingTrySDCAuthState.
    # The optional upl_logging_data is instrumentation, not user payment data.
    variables = {'input': {
        'billable_account_payment_legacy_account_id': payment_account,
        'credential_id': credential,
        'intent': 'SFI',
    }}
    try:
        response = await web.graphql(SDC_SEND_DOC_ID, variables,
            friendly_name='BillingRiskUtilsSendSDCAuthMutation',
            endpoint_url=BILLING_GRAPHQL, business_context_id=business)
    except Exception:
        await ledger.mark(attempt, 'RESULT_UNKNOWN')
        return {**common, 'status': 'ACTION_REQUIRED',
                'code': 'SDC_AUTH_RESULT_UNKNOWN',
                'submitted': True, 'verification_triggered': None,
                'verification_stage': 'RESULT_UNKNOWN'}
    sent = (response.get('data', {}).get('send_dynamic_descriptor_auth', {}).get('sent')
            if isinstance(response, dict) and isinstance(response.get('data'), dict) else None)
    if not isinstance(response, dict) or response.get('errors') or type(sent) is not bool:
        await ledger.mark(attempt, 'RESULT_UNKNOWN')
        return {**common, 'status': 'ACTION_REQUIRED',
                'code': 'SDC_AUTH_RESULT_UNKNOWN',
                'submitted': True, 'verification_triggered': None,
                'verification_stage': 'RESULT_UNKNOWN'}
    if sent:
        await ledger.mark(attempt, 'CHALLENGE_READY')
        return {**common, 'status': 'ACTION_REQUIRED',
                'code': 'SDC_AUTH_SENT_WAIT_CODE',
                'submitted': True, 'verification_triggered': True,
                'verification_stage': 'CHALLENGE_READY'}
    await ledger.mark(attempt, 'SERVER_REJECTED')
    return {**common, 'status': 'BLOCKED',
            'code': 'SDC_AUTH_REJECTED', 'submitted': False,
            'verification_stage': 'SERVER_REJECTED'}


async def _verify_sdc_code(web, *, code, account, payment_account,
                           business, credential, common):
    # No automatic code guessing or retries. Never log/persist the code.
    candidate = str(code or '').strip().upper()
    if re.fullmatch(r'[A-Z0-9]{4}', candidate) is None:
        return {**common, 'code': 'SDC_CODE_REQUIRED'}
    response = await web.graphql(SDC_VERIFY_CODE_DOC_ID, {
        'input': {'credential_id': credential, 'intent': 'SFI',
                  'payment_account_id': payment_account, 'verification_code': candidate},
        'paymentAccountID': payment_account,
    }, friendly_name='useBillingVerifySDCCodeMutation',
       endpoint_url=BILLING_GRAPHQL, business_context_id=business)
    if not isinstance(response, dict) or response.get('errors'):
        return {**common, 'status': 'ACTION_REQUIRED',
                'code': 'SDC_CODE_RESULT_UNVERIFIED'}
    body = response.get('data', {}).get('billing_verify_sdc_code')
    if not isinstance(body, dict):
        return {**common, 'status': 'ACTION_REQUIRED',
                'code': 'SDC_CODE_RESULT_UNVERIFIED'}
    if body.get('verified') is True:
        return {**common, 'status': 'VERIFIED',
                'code': 'SDC_CODE_VERIFIED_BY_META',
                'card_confirmation_status': 'CLEAR',
                'verification_stage': 'META_CONFIRMED',
                'submitted': True}
    remaining = body.get('num_tries_left')
    return {**common, 'status': 'ACTION_REQUIRED',
            'code': 'SDC_CODE_REJECTED',
            'remaining_attempts': remaining if type(remaining) is int and 0 <= remaining <= 20 else None}


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
    operation = payload.get('operation', 'verify')
    if operation not in ('verify', 'authorize', 'verify_code'):
        raise ValueError('CARD_OPERATION_INVALID')
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
            # Meta's historical Billing Hub account query can reject even
            # though a previous Save has confirmed this exact account. Reuse
            # durable evidence ONLY for the same vault card and BM; still
            # demand an independent, live READ_METHODS ownership proof below.
            retained_intent = None
            saved = {}
            if state is not None and getattr(state, 'path', None):
                retained_intent = await CardIntentLedger(state.path).source_read_intent(profile, target)
                if retained_intent and retained_intent.get('card_id') == card_id:
                    try:
                        saved = json.loads(retained_intent['result'])
                    except (ValueError, TypeError):
                        saved = {}
            evidence = None
            if (isinstance(saved, dict) and saved.get('account_id') == target
                    and saved.get('account_scope_verified') is True
                    and saved.get('business_id') == business
                    and re.fullmatch(r'\d{5,30}', str(saved.get('payment_account_id', '')))
                    and isinstance(saved.get('payment_account_node_id'), str)):
                evidence = {key: saved[key] for key in (
                    'account_id', 'account_scope_verified', 'payment_account_id',
                    'payment_account_node_id')}
            if evidence is None:
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
            credential = saved.get('credential') if isinstance(saved, dict) else None
            if (saved.get('account_id') == target and saved.get('business_id') == business
                    and saved.get('payment_account_id') == evidence['payment_account_id']
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
            sdc = sdc_candidate_proof(
                await execute(web, 'READ_SDC_CANDIDATES',
                              payment=evidence['payment_account_id'], business_id=business),
                chosen['credential_id'], *card)
            common = {**common, **sdc}
            if not sdc['sdc_screen_verified']:
                return {**common, 'code': 'CARD_VERIFICATION_SDC_READ_UNVERIFIED'}
            if sdc['sdc_candidate'] and not sdc['sdc_credential_match']:
                return {**common, 'code': 'CARD_VERIFICATION_SDC_CARD_UNVERIFIED'}
            if sdc['sdc_candidate']:
                usability = sdc.get('sdc_usability')
                if usability == 'PENDING_VERIFICATION':
                    if operation == 'authorize':
                        # Meta's own screen skips sending a second hold in
                        # this state, proceeding to the descriptor code.
                        return {**common, 'status': 'ACTION_REQUIRED',
                                'code': 'SDC_AUTH_PENDING_WAIT_CODE',
                                'verification_stage': 'PENDING_VERIFICATION'}
                    if operation == 'verify_code':
                        return await _verify_sdc_code(web,
                            code=payload.get('verification_code'),
                            account=target, payment_account=evidence['payment_account_id'],
                            business=business, credential=chosen['credential_id'],
                            common=common)
                    return {**common, 'status': 'ACTION_REQUIRED',
                            'code': 'SDC_AUTH_PENDING_WAIT_CODE',
                            'verification_stage': 'PENDING_VERIFICATION'}
                if usability == 'UNVERIFIED_OR_PENDING_AUTH':
                    if operation == 'verify_code':
                        return {**common, 'code': 'SDC_AUTH_NOT_STARTED'}
                    if operation == 'authorize':
                        return await _send_sdc_once(web, state=state,
                            profile=profile, account=target,
                            payment_account=evidence['payment_account_id'],
                            business=business, credential=chosen['credential_id'],
                            card_id=card_id, common=common)
                    return {**common, 'status': 'ACTION_REQUIRED',
                            'code': 'SDC_AUTH_READY',
                            'verification_stage': 'UNVERIFIED_OR_PENDING_AUTH'}
                return {**common, 'code': 'SDC_UNSUPPORTED_USABILITY'}
            if operation != 'verify':
                return {**common, 'code': 'SDC_NO_AUTH_REQUIRED'}
            if (chosen.get('needs_verification') is False
                    and chosen.get('verification_tasks_observed') is True
                    and not chosen.get('verification_tasks')
                    and not sdc['sdc_candidate']):
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
