"""HTTP card executor. No browser, dynamic document lookup, funding or replay.

Kept outside the public dispatcher until the runtime Save context is confirmed.
An exact account precheck alone is not evidence of consent or builder compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import uuid

from .payment_card_input import build_save_input, card_auth_fields
from .payment_ptt import encrypt_card_token
from .private_auth import private_auth_error
from .static_payment_card import card_screen_proof, read_card_screen, save_response_proof, confirm_saved_card
from .static_payment_read import ENDPOINT, _identity, _clean_payload, execute, account_proof, methods_proof, _brand

KEY_DOC_ID = '23994203586844376'
SAVE_DOC_ID = '28619313357728847'


@dataclass(frozen=True)
class SaveContext:
    """Internal per-attempt evidence; never accepted directly from an API payload."""
    payment: str
    country: str
    currency: str
    client_info: object = field(repr=False)
    logging_data: dict = field(repr=False)
    include_new_fragment: bool
    # Remains false until the current billing builder/caller context is confirmed.
    runtime_verified: bool = False
    country_policy_verified: bool = False
    usability_intent: str | None = None
    network_consent: bool | None = None
    recurring_consent: bool | None = None


def key_command(payment, session_id):
    return {'input': {'client_mutation_id': str(uuid.uuid4()), 'device_id': 'device_id',
        'payment_type': 'BILLING_WIZARD', 'target_account_id': _identity(payment),
        'fetch_unified_wallet_key': False, 'logging_id': session_id}}


async def save_card_http(web, *, account, business_id, values, context, persist_submit_intent):
    """One Save attempt; independent read confirms the exact returned credential.

The caller must hold a durable per-profile/RK/card lock. The callback must
persist a no-replay intent before POST; any failure stops the submission.
Neither exceptions nor responses may export token/PAN/CVV/bank parameters.
"""
    account, business_id = _identity(account), _identity(business_id)
    base = {'account_id': account, 'business_id': business_id, 'submitted': False,
            'browser_started': False, 'funding_verified': False, 'status': 'BLOCKED'}
    if (not isinstance(context, SaveContext) or context.runtime_verified is not True
            or context.country_policy_verified is not True
            or type(context.include_new_fragment) is not bool):
        return {**base, 'code': 'CARD_PRIVATE_RUNTIME_CONTEXT_UNCONFIRMED'}
    if not callable(persist_submit_intent):
        return {**base, 'code': 'CARD_DURABLE_INTENT_REQUIRED'}
    entered_submit = False
    saved = None
    async def before_submit():
        nonlocal entered_submit
        await persist_submit_intent()
        entered_submit = True
    try:
        auth, secret = card_auth_fields(values)
        web.private_only = True
        evidence = account_proof(await execute(web, 'READ_ACCOUNT', account=account,
                                              business_id=business_id), account)
        if evidence.get('account_scope_verified') is not True:
            return {**base, 'code': evidence['code']}
        payment = evidence['payment_account_id']
        if payment != _identity(context.payment):
            return {**base, 'code': 'CARD_SAVE_SCOPE_UNVERIFIED'}
        methods = methods_proof(await execute(web, 'READ_METHODS', account=account, payment=payment,
                                             business_id=business_id), account, business_id=business_id,
                                             account_evidence=evidence)
        if not all(methods.get(k) is True for k in ('account_scope_verified', 'business_scope_verified',
                'payment_account_relation_verified', 'methods_query_verified')):
            return {**base, 'code': methods['code']}
        # Existing masked matches are ambiguous and cannot authorize another Save.
        if any(row.get('last4') == values['number'][-4:] for row in methods.get('payment_methods', [])):
            return {**base, 'code': 'CARD_MASK_COLLISION_PREEXISTING'}
        screen = card_screen_proof(await read_card_screen(web, business_id=business_id, payment=payment),
                                  account, account_evidence=evidence)
        if screen.get('card_form_verified') is not True:
            return {**base, 'code': screen['code']}
        if screen['options']['verify_tokenization_required'] and context.network_consent is not True:
            return {**base, 'code': 'CARD_TOKENIZATION_CONSENT_REQUIRED'}
        key_vars = key_command(payment, str(uuid.uuid4()))
        payload = await web.graphql(KEY_DOC_ID, key_vars,
            friendly_name='PaymentsCometGetServerEncryptionKeyMutation', endpoint_url=ENDPOINT,
            business_context_id=business_id)
        root = payload.get('data', {}).get('get_server_encryption_key') if isinstance(payload, dict) else None
        if (not _clean_payload(payload) or not isinstance(root, dict) or root.get('payments_error') is not None
                or root.get('client_mutation_id') != key_vars['input']['client_mutation_id']):
            return {**base, 'code': 'CARD_PTT_KEY_RESPONSE_UNCONFIRMED'}
        token = encrypt_card_token(auth, secret, root.get('trust_chain'))
        input_value = build_save_input(values, payment=payment, country=context.country, currency=context.currency,
            token=token, client_info=context.client_info, logging_data=context.logging_data,
            usability_intent=context.usability_intent, network_consent=context.network_consent,
            recurring_consent=context.recurring_consent)
        variables = {'input': input_value, 'getRiskVerificationInfoForAllCredentialsOnPaymentAccount': True,
                     'paymentAccountID': payment, 'includeCreateNewFromOldFragment': context.include_new_fragment}
        payload = await web.graphql(SAVE_DOC_ID, variables, friendly_name='BillingSaveCardCredentialStateMutation',
            endpoint_url=ENDPOINT, business_context_id=business_id, before_submit=before_submit)
        # A transport must invoke the durable hook before attempting Save.
        if not entered_submit:
            return {**base, 'status': 'SUBMITTED_UNVERIFIED', 'submitted': None,
                    'retry_blocked': True, 'code': 'CARD_TRANSPORT_INTENT_UNCONFIRMED'}
        number = values['number']
        # Narrow supported card families; an unknown brand cannot commit linkage.
        import re
        brand = _brand('visa' if number.startswith('4') else 'amex' if re.match(r'^3[47]', number)
                       else 'mastercard' if re.match(r'^(?:5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]))', number)
                       else 'discover' if re.match(r'^(?:6011|65|64[4-9])', number) else '')
        saved = save_response_proof(payload, account, account_evidence=evidence,
                                    expected_card={'type': brand, 'last4': values['number'][-4:]})
        if saved['status'] != 'VERIFYING':
            return {**base, **saved}
        verified = methods_proof(await execute(web, 'READ_METHODS', account=account, payment=payment,
                                              business_id=business_id), account, business_id=business_id,
                                              account_evidence=evidence)
        result = confirm_saved_card(saved, verified, business_id=business_id)
        if result.get('status') == 'LINKED':
            result['funding'] = verified
        return {**base, **result}
    except BaseException as exc:
        if not isinstance(exc, Exception):
            # Caller timeout keeps the already persisted intent; propagate cancellation.
            raise
        # Cancellation can occur after a successful submit hook. Keep its guard.
        if entered_submit:
            if isinstance(saved, dict) and saved.get('status') == 'VERIFYING':
                return {**base, **saved, 'status': 'SUBMITTED_UNVERIFIED',
                        'code': 'CARD_SAVE_LINK_VERIFICATION_PENDING'}
            return {**base, 'submitted': True, 'retry_blocked': True,
                    'status': 'SUBMITTED_UNVERIFIED', 'code': 'CARD_SAVE_RESULT_UNKNOWN'}
        auth_error = private_auth_error(exc)
        allowed = {'CARD_DATA_INVALID', 'CARD_PTT_TRUST_CHAIN_INVALID', 'CARD_PTT_REQUIRED',
                   'CARD_BILLING_COUNTRY_REQUIRED', 'CARD_BILLING_CURRENCY_REQUIRED',
                   'CARD_LOGGING_CONTEXT_REQUIRED', 'CARD_CONSENT_INVALID', 'CARD_USABILITY_INTENT_INVALID'}
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in allowed else 'CARD_HTTP_UNAVAILABLE'
        return {**base, 'code': auth_error.code if auth_error is not None else code}
