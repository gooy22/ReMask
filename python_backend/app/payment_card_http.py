"""HTTP card executor. No browser, dynamic document lookup, funding or replay.

The public adapter enables only its bounded runtime verification path.
An exact account precheck alone is not evidence of consent or builder compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import uuid

from .payment_card_input import build_save_input, card_auth_fields, validate_client_info
from .payment_card_requirements import read_card_requirements, requirements_proof, resolve_country_policy, confirm_bin_country
from .payment_ptt import encrypt_card_token
from .private_auth import private_auth_error
from .static_payment_card import card_screen_proof, read_card_screen, save_response_proof, confirm_saved_card
from .static_payment_read import ENDPOINT, _identity, _clean_payload, execute, account_proof, methods_proof, _brand

KEY_DOC_ID = '23994203586844376'
SAVE_DOC_ID = '28619313357728847'


def number_brand(number):
    import re
    return _brand('visa' if number.startswith('4') else 'amex' if re.match(r'^3[47]', number)
        else 'mastercard' if re.match(r'^(?:5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]))', number)
        else 'discover' if re.match(r'^(?:6011|65|64[4-9])', number) else '')


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
    usability_intent: str | None = None
    network_consent: bool | None = None
    recurring_consent: bool | None = None


def key_command(payment, session_id):
    return {'input': {'client_mutation_id': str(uuid.uuid4()), 'device_id': 'device_id',
        'payment_type': 'BILLING_WIZARD', 'target_account_id': _identity(payment),
        'fetch_unified_wallet_key': False, 'logging_id': session_id}}


async def save_card_http(web, *, account, business_id, values, context, persist_submit_intent, retain_verification_context=None):
    """One Save attempt; independent read confirms the exact returned credential.

The caller must hold a durable per-profile/RK/card lock. The callback must
persist a no-replay intent before POST; any failure stops the submission.
Neither exceptions nor responses may export token/PAN/CVV/bank parameters.
"""
    account, business_id = _identity(account), _identity(business_id)
    base = {'account_id': account, 'business_id': business_id, 'submitted': False,
            'browser_started': False, 'funding_verified': False, 'status': 'BLOCKED'}
    if (not isinstance(context, SaveContext) or context.runtime_verified is not True
            or type(context.include_new_fragment) is not bool):
        return {**base, 'code': 'CARD_PRIVATE_RUNTIME_CONTEXT_UNCONFIRMED'}
    if not callable(persist_submit_intent):
        return {**base, 'code': 'CARD_DURABLE_INTENT_REQUIRED'}
    entered_submit = False
    saved = None
    def stage(name):
        logging.getLogger('remask.payment_card').info(
            'card HTTP stage account=%s business=%s stage=%s browser_started=False', account, business_id, name)
    async def before_submit():
        nonlocal entered_submit
        await persist_submit_intent()
        entered_submit = True
    try:
        validate_client_info(context.client_info)
        auth, secret = card_auth_fields(values)
        web.private_only = True
        stage('PRECHECK_ACCOUNT')
        evidence = account_proof(await execute(web, 'READ_ACCOUNT', account=account,
                                              business_id=business_id), account)
        if evidence.get('account_scope_verified') is not True:
            return {**base, 'code': evidence['code']}
        payment = evidence['payment_account_id']
        if payment != _identity(context.payment):
            return {**base, 'code': 'CARD_SAVE_SCOPE_UNVERIFIED'}
        stage('PRECHECK_METHODS')
        methods = methods_proof(await execute(web, 'READ_METHODS', account=account, payment=payment,
                                             business_id=business_id), account, business_id=business_id,
                                             account_evidence=evidence)
        if not all(methods.get(k) is True for k in ('account_scope_verified', 'business_scope_verified',
                'payment_account_relation_verified', 'methods_query_verified')):
            return {**base, 'code': methods['code']}
        # Existing masked matches are ambiguous and cannot authorize another Save.
        if any(row.get('last4') == values['number'][-4:] for row in methods.get('payment_methods', [])):
            return {**base, 'code': 'CARD_MASK_COLLISION_PREEXISTING'}
        if retain_verification_context is not None:
            # Metadata needed to reconcile a lost Save reply, never card input.
            await retain_verification_context({**evidence, 'business_id':business_id,
                'last4':values['number'][-4:], 'expected_card_type':number_brand(values['number']), 'preexisting_credential_ids':
                [row['credential_id'] for row in methods.get('payment_methods', [])]})
        stage('PRECHECK_CARD_SCREEN')
        screen_payload = await read_card_screen(web, business_id=business_id, payment=payment)
        screen = card_screen_proof(screen_payload, account, account_evidence=evidence)
        if screen.get('card_form_verified') is not True:
            return {**base, 'code': screen['code']}
        policy = await resolve_country_policy(web, screen_payload, business_id=business_id,
                                             evidence=evidence, country=context.country)
        if policy.get('country_policy_verified') is not True and not policy.get('bin_country_required'):
            return {**base, 'code': policy['code']}
        if screen['options']['verify_tokenization_required'] and context.network_consent is not True:
            return {**base, 'code': 'CARD_TOKENIZATION_CONSENT_REQUIRED'}
        stage('PRECHECK_BIN_REQUIREMENTS')
        requirements = requirements_proof(await read_card_requirements(web, business_id=business_id,
            payment=payment, number=values['number'], country=context.country, currency=context.currency),
            values=values, is_prepaid_only=policy['is_prepaid_only'], recurring_consent=context.recurring_consent)
        if requirements.get('card_requirements_verified') is not True:
            return {**base, 'code': requirements['code'], 'missing_fields': requirements['required_fields']}
        key_vars = key_command(payment, str(uuid.uuid4()))
        stage('PTT_KEY')
        payload = await web.graphql(KEY_DOC_ID, key_vars,
            friendly_name='PaymentsCometGetServerEncryptionKeyMutation', endpoint_url=ENDPOINT,
            business_context_id=business_id)
        data = payload.get('data') if isinstance(payload, dict) else None
        root = data.get('get_server_encryption_key') if isinstance(data, dict) else None
        if not _clean_payload(payload) or not isinstance(root, dict):
            return {**base, 'code': 'CARD_PTT_KEY_RESPONSE_UNCONFIRMED'}
        if root.get('payments_error') is not None:
            return {**base, 'code': 'CARD_PTT_KEY_REJECTED'}
        # Current FBPay consumer uses the trust chain, not the nullable Relay echo.
        # A foreign non-null echo still rejects the response. Encryption below
        # independently validates every certificate against the pinned Meta CA.
        if root.get('client_mutation_id') not in (None, key_vars['input']['client_mutation_id']):
            return {**base, 'code': 'CARD_PTT_KEY_MUTATION_MISMATCH'}
        if root.get('dev_external') is True:
            return {**base, 'code': 'CARD_PTT_DEVELOPMENT_KEY_REJECTED'}
        stage('PTT_ENCRYPT')
        token = encrypt_card_token(auth, secret, root.get('trust_chain'))
        if policy.get('bin_country_required'):
            stage('PRECHECK_BIN_COUNTRY')
            country_result = await confirm_bin_country(web, business_id=business_id,
                payment=payment, number=values['number'], token=token, country=context.country)
            if country_result.get('country_policy_verified') is not True:
                return {**base, 'code': country_result['code']}
        input_value = build_save_input(values, payment=payment, country=context.country, currency=context.currency,
            token=token, client_info=context.client_info, logging_data=context.logging_data,
            usability_intent=context.usability_intent, network_consent=context.network_consent,
            recurring_consent=context.recurring_consent)
        variables = {'input': input_value, 'getRiskVerificationInfoForAllCredentialsOnPaymentAccount': True,
                     'paymentAccountID': payment, 'includeCreateNewFromOldFragment': context.include_new_fragment}
        stage('SAVE')
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
        stage('VERIFY_EXACT_CREDENTIAL')
        verified = methods_proof(await execute(web, 'READ_METHODS', account=account, payment=payment,
                                              business_id=business_id), account, business_id=business_id,
                                              account_evidence=evidence)
        result = confirm_saved_card(saved, verified, business_id=business_id)
        if result.get('status') == 'LINKED':
            stage('COMMIT_LINKED')
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
                   'CARD_LOGGING_CONTEXT_REQUIRED', 'CARD_CONSENT_INVALID', 'CARD_USABILITY_INTENT_INVALID',
                   'CARD_CLIENT_CONTEXT_REQUIRED'}
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in allowed else 'CARD_HTTP_UNAVAILABLE'
        return {**base, 'code': auth_error.code if auth_error is not None else code}
