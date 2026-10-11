"""Meta's issuer challenge, independent of SDC and Add Funds.

Contracts observed in public Meta callers on 2026-10-10:
useBillingRiskInitThreeDSMutation (f358e2c3...),
useBillingRiskVerifyThreeDSMutation (52da159e...),
BillingRiskVerifyThreeDSPageViewManager (398e071f...).
No amount is supplied by ReMask. Only Meta chooses the issuer challenge.
Challenge form values are ephemeral and must never enter a vault or log.
"""
import re
from urllib.parse import urlsplit

from .payment_card_intents import BankVerificationLedger
from .private_auth import private_auth_error

ENDPOINT = 'https://business.facebook.com/api/graphql/'
PAGE_DOC = '26336474412707714'
INIT_DOC = '28721030687535476'
VERIFY_DOC = '28694795333509169'


async def bank_page_proof(web, *, payment, business, node, credential):
    web.private_only = True
    response = await web.graphql(PAGE_DOC,
        {'paymentAccountID': payment, 'paymentMethodID': node},
        friendly_name='BillingRiskVerifyThreeDSPageViewManagerQuery',
        endpoint_url=ENDPOINT, business_context_id=business)
    if not isinstance(response, dict) or response.get('errors') or response.get('error') or response.get('hasNext') is True:
        return None
    data = response.get('data')
    account = data.get('payment_account') if isinstance(data, dict) else None
    card = data.get('node') if isinstance(data, dict) else None
    billable = account.get('billable_account') if isinstance(account, dict) else None
    if (not isinstance(account, dict) or account.get('payment_legacy_account_id') != payment
            or not isinstance(card, dict) or card.get('credential_id') != credential
            or card.get('id', node) != node
            or card.get('__typename', 'ExternalCreditCard') != 'ExternalCreditCard'
            or not isinstance(billable, dict)
            or not re.fullmatch(r'[A-Z]{3}', str(billable.get('currency', '')))):
        return None
    return {'currency': billable['currency'], 'bank_page_verified': True}


def _body(response, field):
    if not isinstance(response, dict) or response.get('errors') or response.get('error') or response.get('hasNext') is True:
        return None
    data = response.get('data')
    value = data.get(field) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else None


def _client_success(body):
    client = body.get('client_result') if isinstance(body, dict) else None
    # Same success branch as handleBelieveMutationResult. Error, deferred and
    # next-task results never become a verified card.
    return (isinstance(client, dict) and client.get('__typename') in
            ('XFBIBillingClientSuccess', 'XFBBillingMutationSuccess')
            and not client.get('next_tasks'))


def _challenge(request, token):
    if not isinstance(request, dict):
        return None
    uri, params = request.get('url'), request.get('params')
    if not isinstance(uri, str) or len(uri) > 12000 or not isinstance(params, list) or len(params) > 80:
        return None
    try:
        parsed = urlsplit(uri)
        # The initial form receives the profile's Meta CSRF token. Refuse to
        # send it to an issuer or any other host. Meta can redirect its iframe
        # to the actual bank, exactly as its own verification UI does.
        if (parsed.scheme != 'https' or not (parsed.hostname == 'facebook.com'
                or (parsed.hostname or '').endswith('.facebook.com'))
                or parsed.username or parsed.password or parsed.port or parsed.fragment):
            return None
    except ValueError:
        return None
    clean, keys = [], set()
    for row in params:
        if (not isinstance(row, dict) or not isinstance(row.get('key'), str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', row['key'])
                or not isinstance(row.get('value'), str) or len(row['value']) > 100000
                or row['key'] in keys or row['key'] == 'fb_dtsg'):
            return None
        keys.add(row['key']); clean.append({'key': row['key'], 'value': row['value']})
    if clean:
        if not isinstance(token, str) or not token:
            return None
        clean.append({'key': 'fb_dtsg', 'value': token})
    return {'url': uri, 'params': clean}


async def bank_existing(state, *, profile, account, payment_account, credential, card_id):
    if state is None or not getattr(state, 'path', None):
        raise ValueError('BANK_DURABLE_GUARD_REQUIRED')
    return await BankVerificationLedger(state.path).read_existing(
        profile=profile, account=account, payment_account=payment_account,
        credential=credential, card_id=card_id, flow='THREEDS')


async def start_bank_once(web, *, state, profile, account, payment_account,
                          business, credential, card_id, currency, common):
    if common.get('bank_page_verified') is not True:
        return {**common, 'code': 'BANK_PAGE_UNVERIFIED'}
    ledger = BankVerificationLedger(state.path)
    attempt = None
    refused = None

    async def before_submit():
        nonlocal attempt, refused
        reservation = await ledger.reserve(profile=profile, account=account,
            payment_account=payment_account, credential=credential, card_id=card_id, flow='THREEDS')
        if not reservation['same_card'] or not reservation['reserved']:
            refused = {**common, 'status': 'ACTION_REQUIRED', 'code': 'BANK_AUTH_ALREADY_ATTEMPTED',
                       'verification_stage': reservation['stage']}
            raise ValueError('BANK_AUTH_ALREADY_ATTEMPTED')
        if not await ledger.mark(reservation['attempt_id'], 'REQUEST_SENT'):
            raise ValueError('BANK_AUTH_RESERVATION_LOST')
        attempt = reservation['attempt_id']

    try:
        response = await web.graphql(INIT_DOC, {'input': {
            'account_currency': currency, 'authentication_response_type': 'REDIRECT',
            'authentication_response_url': '', 'credential_id': credential,
            'payment_account_id': payment_account, 'payment_item_type': 'MOR_ADS_INVOICE'}},
            friendly_name='useBillingRiskInitThreeDSMutation', endpoint_url=ENDPOINT,
            business_context_id=business, before_submit=before_submit)
    except Exception as exc:
        if refused:
            return refused
        if attempt is None:
            auth = private_auth_error(exc)
            return {**common, 'code': auth.code if auth else 'BANK_AUTH_PRECHECK_UNAVAILABLE'}
        await ledger.mark(attempt, 'RESULT_UNKNOWN')
        return {**common, 'status': 'ACTION_REQUIRED', 'code': 'BANK_AUTH_RESULT_UNKNOWN',
                'submitted': True, 'verification_triggered': None, 'verification_stage': 'RESULT_UNKNOWN'}
    if attempt is None:
        return {**common, 'code': 'BANK_AUTH_SUBMIT_FENCE_MISSING'}
    body = _body(response, 'risk_init_credential_verification')
    bootstrap = getattr(web, '_bootstrap', None)
    challenge = _challenge(body.get('authentication_request') if body else None,
                           getattr(bootstrap, 'fb_dtsg', None))
    if not _client_success(body) or challenge is None:
        await ledger.mark(attempt, 'RESULT_UNKNOWN')
        return {**common, 'status': 'ACTION_REQUIRED', 'code': 'BANK_AUTH_RESULT_UNKNOWN',
                'submitted': True, 'verification_triggered': None, 'verification_stage': 'RESULT_UNKNOWN'}
    await ledger.mark(attempt, 'CHALLENGE_READY')
    return {**common, 'status': 'ACTION_REQUIRED', 'code': 'BANK_AUTH_CHALLENGE_READY',
            'submitted': True, 'verification_triggered': True, 'verification_stage': 'CHALLENGE_READY',
            'bank_challenge': challenge}


async def verify_bank_result(web, *, state, profile, account, payment_account,
                             business, credential, card_id, common):
    # Can also reconcile a challenge the user opened directly in Meta.
    # This mutation validates only; it never initializes a bank authorization.
    sent = False
    async def before_submit():
        nonlocal sent
        sent = True
    try:
        response = await web.graphql(VERIFY_DOC, {
            'input': {'credential_id': credential, 'payment_legacy_account_id': payment_account},
            'paymentAccountID': payment_account},
            friendly_name='useBillingRiskVerifyThreeDSMutation', endpoint_url=ENDPOINT,
            business_context_id=business, before_submit=before_submit)
    except Exception as exc:
        auth = private_auth_error(exc)
        return {**common, 'status': 'ACTION_REQUIRED',
                'code': auth.code if auth else 'BANK_VERIFY_RESULT_UNKNOWN', 'submitted': sent}
    body = _body(response, 'validate_risk_credential_verification')
    if not sent or not body or body.get('verified') is not True or not _client_success(body):
        return {**common, 'status': 'ACTION_REQUIRED', 'code': 'BANK_VERIFICATION_NOT_CONFIRMED',
                'submitted': sent, 'verification_stage': 'AWAITING_BANK_RESULT'}
    recorded = await BankVerificationLedger(state.path).record_meta_confirmation(
        profile=profile, account=account, payment_account=payment_account,
        credential=credential, card_id=card_id, flow='THREEDS')
    if not recorded:
        return {**common, 'code': 'BANK_CONFIRMATION_SCOPE_CONFLICT', 'submitted': sent}
    return {**common, 'status': 'VERIFIED', 'code': 'BANK_CARD_VERIFIED_BY_META',
            'submitted': True, 'verification_stage': 'META_CONFIRMED',
            'bank_verified_by_meta': True, 'card_confirmation_status': 'CLEAR',
            'verification_tasks': []}
