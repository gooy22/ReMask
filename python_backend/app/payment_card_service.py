"""Public card service: selected, profile/proxy-bound RK over HTTP only.

Any explicitly selected RK must independently pass the live Meta account,
Business, payment-scope, card-policy and durable no-replay verification.
No profile-specific allowlist or cross-RK success dependency is used.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid

from .payment_card_http import SaveContext, save_card_http
from .payment_card_input import validate_client_info, card_auth_fields
from .payment_card_intents import CardIntentLedger
from .payment_inspection import account_id, resolve_payment_asset
from .private_auth import private_auth_error
from .payment_country_setup import configure_country
from .static_payment_card import prepare_profile_card_form, confirm_saved_card
from .payment_card_business_wallet import inspect_business_wallet_card, inspect_save_reply_card_identity
from .static_payment_read import execute, account_proof, payment_page_proof, inspect_methods, complete_methods

def setup_context(payload, target, payment):
    if not payment_page_proof(payload, target, payment):
        raise ValueError('CARD_SETUP_SCOPE_UNVERIFIED')
    account = payload['data']['payment_account']['billable_account']
    tax = account.get('billable_account_tax_info')
    country = tax.get('business_country_code') if isinstance(tax, dict) else None
    currency = account.get('currency')
    # No silent default country and no country/currency/timezone mutation.
    if (not isinstance(country, str) or not re.fullmatch(r'[A-Z]{2}', country)
            or not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency)):
        raise ValueError('PAYMENT_ACCOUNT_SETUP_REQUIRED')
    return country, currency


async def profile_payment_card_http(resolver, profile, payload, *, state=None):
    from .session import ProfileSession
    target = account_id(payload.get('account_id', ''))
    base = {'profile_id': profile, 'account_id': target, 'status': 'BLOCKED',
            'submitted': False, 'browser_started': False, 'funding_verified': False}
    operation = payload.get('operation')
    if operation == 'prepare':
        return await prepare_profile_card_form(resolver, profile, target, state=state,
                                              asset_hint=payload.get('asset_hint'))
    if operation not in {'bind', 'reconcile'}:
        raise ValueError('CARD_OPERATION_INVALID')
    ledger = None
    attempt_id = payload.get('attempt_id')
    card_id = payload.get('card_id')
    result = None
    try:
        async with asyncio.timeout(105):
            if operation == 'bind' and (not isinstance(attempt_id, str) or not re.fullmatch(r'[a-f0-9]{24}', attempt_id)):
                return {**base, 'code': 'CARD_DURABLE_INTENT_REQUIRED'}
            if not isinstance(card_id, str) or not re.fullmatch(r'card_[a-f0-9]{24}', card_id):
                return {**base, 'code': 'CARD_DURABLE_INTENT_REQUIRED'}
            if state is None or not getattr(state, 'path', None):
                return {**base, 'code': 'CARD_DURABLE_INTENT_REQUIRED'}
            ledger = CardIntentLedger(state.path)
            pending = await ledger.pending(profile, target)
            confirmed_read = False
            if operation == 'reconcile' and not pending:
                pending = await ledger.confirmed_intent(profile, target, card_id)
                confirmed_read = pending is not None
            if operation == 'reconcile' and not pending:
                return {**base, 'code': 'CARD_HTTP_INTENT_NOT_FOUND'}
            if pending:
                retained = {**base, 'status': 'SUBMITTED_UNVERIFIED', 'submitted': True,
                            'retry_blocked': True, 'code': 'CARD_BINDING_RECONCILE_REQUIRED'}
                saved = json.loads(pending['result'])
                for key in ('save_response_stage','meta_error_codes','meta_error_messages','verification_stage'):
                    if key in saved:
                        retained[key] = saved[key]
                if pending['card_id'] != card_id or not (saved.get('credential') or saved.get('preexisting_credential_ids') is not None):
                    return {**retained, 'status': 'ACTION_REQUIRED' if saved.get('status') == 'ACTION_REQUIRED' else retained['status'],
                            'code': 'CARD_BANK_CONFIRMATION_REQUIRED' if saved.get('status') == 'ACTION_REQUIRED' else retained['code']}
                asset = await resolve_payment_asset(profile, target, state, payload.get('asset_hint'))
                if not asset:
                    return retained
                profile_context = await resolver.resolve(profile)
                async with ProfileSession(profile_context, timeout_seconds=30) as session:
                    web = await session.facebook_web()
                    methods = await inspect_methods(web, account=target, business_id=asset['business_id'])
                    # A known Save credential can be positively reverified by
                    # the scoped card query. The complete inventory is needed
                    # for absence/retry decisions, not to discard exact presence.
                    direct = confirm_saved_card({**saved, 'status':'VERIFYING'}, methods, business_id=asset['business_id']) if saved.get('credential') else {}
                    if not (confirmed_read and direct.get('status') == 'LINKED'):
                        methods = await complete_methods(web, methods, business_id=asset['business_id'])
                    import logging
                    logging.getLogger('remask.payment_card').info('card reconcile inventory account=%s code=%s complete=%s credentials=%s cards=%s',
                        target, methods.get('code'), methods.get('inventory_complete'),
                        len(methods.get('all_credential_ids', [])), len(methods.get('payment_methods', [])))
                    retained['funding'] = {**methods, 'profile_id': profile}
                    if (operation == 'bind' and payload.get('reviewed_attempt_id') == pending['attempt_id']
                            and methods.get('inventory_complete') is True
                            and methods.get('all_credential_ids') == []
                            and methods.get('verification_status') == 'NONE'
                            and saved.get('status') != 'ACTION_REQUIRED'):
                        await ledger.review_empty(pending['attempt_id'], profile, target, card_id)
                        return await profile_payment_card_http(
                            resolver, profile,
                            {k:v for k,v in payload.items() if k != 'reviewed_attempt_id'},
                            state=state)
                    if not saved.get('credential'):
                        matches = [row for row in methods.get('payment_methods', [])
                            if row.get('last4') == saved.get('last4') and row.get('type') == saved.get('expected_card_type')
                            and row.get('credential_id') not in saved['preexisting_credential_ids']
                            and (row.get('needs_verification') is False or methods.get('inventory_complete') is True)]
                        if len(matches) != 1:
                            return retained
                        row = matches[0]
                        saved['credential'] = {'id':row['credential_id'], 'type':row['type'], 'last4':row['last4']}
                        if row.get('needs_verification') is True:
                            result = {**saved, **retained, 'credential': saved['credential'],
                                'status':'ACTION_REQUIRED', 'code':'CARD_BANK_CONFIRMATION_REQUIRED'}
                            await ledger.finish(pending['attempt_id'], result)
                            return result
                    exact_ids = {saved['credential'].get(key) for key in ('id', 'credential_id')
                                 if isinstance(saved['credential'].get(key), str)
                                 and saved['credential'][key]}
                    if saved.get('status') == 'ACTION_REQUIRED' and not any(
                            row.get('credential_id') in exact_ids
                            and row.get('needs_verification') is False
                            for row in methods.get('payment_methods', [])):
                        return {**retained, 'status':'ACTION_REQUIRED','code':'CARD_BANK_CONFIRMATION_REQUIRED'}
                    result = confirm_saved_card({**saved, 'status': 'VERIFYING'}, methods, business_id=asset['business_id'])
                    if (result.get('status') == 'VERIFYING'
                            and result.get('verification_stage') == 'save_credential_not_in_rk'
                            and methods.get('inventory_complete') is True):
                        wallet_stage = await inspect_business_wallet_card(
                            web, account=target, business_id=asset['business_id'],
                            saved=saved)
                        if wallet_stage == 'business_wallet_card_not_observed':
                            wallet_stage = await inspect_save_reply_card_identity(
                                web, saved=saved, business_id=asset['business_id'])
                        result['verification_stage'] = wallet_stage
                        # A non-card Meta billing instrument is NOT an attached
                        # credit card. Permit the *explicitly* reviewed, stale
                        # attempt to be retired only after the exact card is
                        # absent in BOTH the complete child RK inventory and
                        # the independently scoped business wallet AND direct saved-card identity read.
                        methods['wallet_reconcile_stage'] = wallet_stage
                        retained['funding'] = {**methods, 'profile_id': profile}
                    can_review_noncard = (
                        result.get('verification_stage') == 'saved_card_identity_not_observed'
                        and methods.get('inventory_complete') is True
                        and methods.get('card_credential_count') == 0
                        and methods.get('payment_methods') == [])
                    can_review_empty = (
                        methods.get('inventory_complete') is True
                        and methods.get('all_credential_ids') == []
                        and methods.get('verification_status') == 'NONE'
                        and saved.get('status') != 'ACTION_REQUIRED')
                    if (operation == 'bind'
                            and payload.get('reviewed_attempt_id') == pending['attempt_id']
                            and saved.get('status') != 'ACTION_REQUIRED'
                            and result.get('status') == 'VERIFYING'
                            and (can_review_empty or can_review_noncard)):
                        await ledger.review_empty(pending['attempt_id'], profile, target, card_id)
                        logging.getLogger('remask.payment_card').info(
                            'card reviewed retry accepted account=%s no_cards=%s wallet_checked=%s',
                            target, methods.get('card_credential_count', 0),
                            bool(methods.get('wallet_reconcile_stage')))
                        return await profile_payment_card_http(
                            resolver, profile,
                            {k:v for k,v in payload.items() if k != 'reviewed_attempt_id'},
                            state=state)
                    logging.getLogger('remask.payment_card').info(
                        'card reconcile verification account=%s status=%s stage=%s',
                        target, result.get('status'), result.get('verification_stage', 'confirmed'))
                    if (result.get('status') == 'VERIFYING'
                            and result.get('verification_stage') == 'saved_card_identity_observed_but_not_linked'):
                        # The original Save credential still exists in Meta.
                        # Do NOT invite another Save or claim a linked RK; the
                        # missing step is explicit attachment of this credential.
                        return {**retained, 'code': 'CARD_SAVED_CREDENTIAL_UNLINKED',
                                'verification_stage': 'saved_card_identity_observed_but_not_linked'}
                    if result.get('status') in {'LINKED', 'ACTION_REQUIRED'}:
                        await ledger.finish(pending['attempt_id'], result)
                        if result['status'] == 'LINKED':
                            await state.set_payment_link_state(profile, target, True, source='card_http_exact_credential')
                        return {**base, **result, 'funding': {**methods, 'profile_id':profile}}
                    return {**retained, **({'verification_stage':result['verification_stage']}
                                         if 'verification_stage' in result else {})}
            raw_card = payload.get('card')
            if not isinstance(raw_card, dict):
                return {**base, 'code': 'CARD_DATA_INVALID'}
            values = {**raw_card, 'cvv': payload.get('cvv')}
            card_auth_fields(values)
            client_info = validate_client_info(payload.get('client_info'))
            asset = await resolve_payment_asset(profile, target, state, payload.get('asset_hint'))
            if not asset:
                return {**base, 'code': 'PAYMENT_ACCOUNT_BINDING_MISSING'}
            profile_context = await resolver.resolve(profile)
            if asset['business_id'] == str(profile_context.cookies.get('c_user') or ''):
                return {**base, 'code': 'PERSONAL_AD_ACCOUNT_EXCLUDED'}
            async with ProfileSession(profile_context, timeout_seconds=30) as session:
                web = await session.facebook_web()
                business = asset['business_id']
                evidence = account_proof(await execute(web, 'READ_ACCOUNT', account=target,
                                                      business_id=business), target)
                if evidence.get('account_scope_verified') is not True:
                    return {**base, 'code': evidence['code']}
                payment = evidence['payment_account_id']
                setup = payload.get('billing_setup')
                setup_payload = await execute(web, 'READ_SETUP', payment=payment, business_id=business)
                configured = await configure_country(web, target=target, business_id=business,
                    evidence=evidence, current_payload=setup_payload, setup=setup)
                import logging
                logging.getLogger('remask.payment_card').info('card country setup result account=%s code=%s',
                    target, configured.get('code'))
                if 'setup_payload' not in configured:
                    return {**base, **{key:configured[key] for key in ('code','meta_error_messages','setup_stage') if key in configured}}
                country, currency = setup_context(configured['setup_payload'], target, payment)
                if isinstance(setup, dict) and setup.get('country_mode') == 'strict' and setup.get('country') != country:
                    return {**base, 'code': 'CARD_BILLING_COUNTRY_MISMATCH'}
                context = SaveContext(payment=payment, country=country, currency=currency,
                    client_info=client_info, logging_data={'target_name':'useBillingAddCreditCardMutation',
                        'user_session_id':str(uuid.uuid4())}, include_new_fragment=False,
                    runtime_verified=True, network_consent=payload.get('network_consent'),
                    recurring_consent=payload.get('recurring_consent'))
                verification_context = {}
                async def retain(proof):
                    verification_context.update(proof)
                async def persist():
                    await ledger.submit(profile, target, card_id, attempt_id, verification_context)
                result = await save_card_http(web, account=target, business_id=business,
                    values=values, context=context, persist_submit_intent=persist,
                    retain_verification_context=retain)
                if isinstance(result.get('funding'), dict):
                    result['funding']['profile_id'] = profile
                own_pending = await ledger.pending(profile, target)
                if own_pending and own_pending['attempt_id'] == attempt_id:
                    await ledger.finish(attempt_id, result)
                if result.get('status') == 'LINKED' and hasattr(state, 'set_payment_link_state'):
                    await state.set_payment_link_state(profile, target, True, source='card_http_exact_credential')
                return {**result, 'profile_id':profile}
    except Exception as exc:
        auth = private_auth_error(exc)
        if isinstance(result, dict) and result.get('submitted') is True:
            return {**base, **result}
        pending = await ledger.pending(profile, target) if ledger is not None else None
        if pending:
            # A failed read must not conceal an auth/transport failure behind
            # the previous Save's unknown result. Retain the submit guard.
            import logging
            cause = getattr(exc, '__cause__', None)
            read_code = auth.code if auth else ('PAYMENT_HTTP_TIMEOUT'
                if isinstance(exc, TimeoutError) or isinstance(cause, TimeoutError)
                else 'PAYMENT_HTTP_UNAVAILABLE')
            stage = getattr(exc, 'transport_stage', '')
            stage = stage if stage in {'business_auth_precheck', 'graphql_post', 'graphql_response'} else 'payment_read'
            logging.getLogger('remask.payment_card').warning(
                'card reconcile failed account=%s code=%s stage=%s exception=%s cause=%s',
                target, read_code, stage, type(exc).__name__, type(cause).__name__)
            return {**base, 'status':'SUBMITTED_UNVERIFIED', 'submitted':True,
                    'retry_blocked':True, 'code':'CARD_SAVE_RESULT_UNKNOWN',
                    'reconcile_error_code':read_code, 'reconcile_stage':stage}
        allowed = {'CARD_DATA_INVALID', 'CARD_CLIENT_CONTEXT_REQUIRED', 'CARD_SETUP_SCOPE_UNVERIFIED',
                   'PAYMENT_ACCOUNT_SETUP_REQUIRED', 'CARD_BINDING_RECONCILE_REQUIRED'}
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in allowed else 'PAYMENT_HTTP_TIMEOUT' if isinstance(exc, TimeoutError) else 'CARD_HTTP_UNAVAILABLE'
        return {**base, 'code':auth.code if auth else code}
