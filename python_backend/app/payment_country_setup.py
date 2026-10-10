"""Apply an explicitly selected billing country to the existing exact RK only.

Current BillingCountryCurrencyDecisionState uses a separate mutation for an
UPDATE; closing the old account and creating another is deliberately unsupported.
Currency/timezone are retained, never replaced by defaults during card binding.
"""
from .static_payment_read import ENDPOINT, _clean_payload, execute, account_proof, payment_page_proof, methods_proof, complete_methods

DECISION_DOC = '28210215908604615'
UPDATE_DOC = '29520642304190454'
INITIALIZE_DOC = '40043022898629616'


async def _initialize_country(web, *, target, business_id, evidence, payload, desired, setup):
    """Use the observed first-setup sender only for a scoped, empty account."""
    import re
    payment = evidence['payment_account_id']
    import logging
    data = payload.get('data') if isinstance(payload, dict) else None
    row = data.get('payment_account') if isinstance(data, dict) else None
    node = row.get('billable_account') if isinstance(row, dict) else None
    tax_info = node.get('billable_account_tax_info') if isinstance(node, dict) else None
    country_value = tax_info.get('business_country_code') if isinstance(tax_info, dict) else None
    config_root = node.get('billing_page_configs') if isinstance(node, dict) else None
    config = config_root.get('country_currency_timezone') if isinstance(config_root, dict) else None
    logging.getLogger('remask.payment_card').info(
        'card country setup account=%s scope=%s country=%s selectable=%s options=%s', target,
        payment_page_proof(payload, target, payment),
        country_value if isinstance(country_value, str) and re.fullmatch(r'[A-Z]{2}', country_value) else 'UNSET',
        config.get('can_select_tax_country') is True if isinstance(config, dict) else False,
        {key:len(node[key]) if isinstance(node, dict) and isinstance(node.get(key), list) else -1
            for key in ('supported_country_options','supported_currency_options','supported_timezone_options')})
    account = payload['data']['payment_account']['billable_account']
    if (evidence.get('account_scope_verified') is not True
            or payload['data']['payment_account'].get('id') != evidence.get('payment_account_node_id')):
        return {'code':'CARD_SETUP_SCOPE_UNVERIFIED'}
    context = _context(payload, target, payment)
    configs = account.get('billing_page_configs')
    config = configs.get('country_currency_timezone') if isinstance(configs, dict) else None
    if not isinstance(config, dict) or config.get('can_select_tax_country') is not True:
        return {'code':'CARD_COUNTRY_INITIAL_SETUP_UNCONFIRMED'}
    if (not isinstance(context, dict) or not isinstance(context['currency'], str)
            or not re.fullmatch(r'[A-Z]{3}', context['currency'])
            or not isinstance(context['timezone'], str) or not context['timezone']
            or setup.get('currency', context['currency']) != context['currency']):
        return {'code':'CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED'}
    for key, selected in [('supported_country_options', desired),
            ('supported_currency_options', context['currency']),
            ('supported_timezone_options', context['timezone'])]:
        options = account.get(key)
        if not isinstance(options, list) or selected not in [x.get('value') for x in options if isinstance(x, dict)]:
            return {'code':'CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED'}
    restrictions = account.get('billing_country_currency_restrictions')
    if not isinstance(restrictions, dict):
        return {'code':'CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED'}
    for key in ('country_currency', 'currency_country'):
        rows = restrictions.get(key)
        if not isinstance(rows, list) or any(not isinstance(x, dict)
                or not isinstance(x.get('country'), str) or not isinstance(x.get('currency'), str) for x in rows):
            return {'code':'CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED'}
        if any((x['country'] == desired and x['currency'] != context['currency'])
                if key == 'country_currency' else (x['currency'] == context['currency'] and x['country'] != desired)
                for x in rows):
            return {'code':'CARD_BILLING_COUNTRY_MISMATCH'}
    ownership = methods_proof(await execute(web, 'READ_METHODS', account=target,
        payment=payment, business_id=business_id), target, business_id=business_id, account_evidence=evidence)
    methods = await complete_methods(web, ownership, business_id=business_id)
    if (methods.get('inventory_complete') is not True or methods.get('all_credential_ids') != []
            or methods.get('verification_status') != 'NONE'):
        return {'code':'CARD_COUNTRY_INITIAL_SETUP_UNCONFIRMED'}
    result = await web.graphql(INITIALIZE_DOC, {'input':{
        'billable_account_payment_legacy_account_id':payment, 'country_code':desired,
        'currency':context['currency'], 'timezone':context['timezone']}, 'paymentAccountID':payment},
        friendly_name='useBillingSetCountryCurrencyMutation', endpoint_url=ENDPOINT,
        business_context_id=business_id)
    data = result.get('data') if isinstance(result, dict) else None
    root = data.get('billable_account_set_country_currency') if isinstance(data, dict) else None
    client = root.get('client_result') if isinstance(root, dict) else None
    returned = root.get('payment_account') if isinstance(root, dict) else None
    from .payment_card_http import save_error_diagnostic
    errors = list(result.get('errors', [])) if isinstance(result, dict) and isinstance(result.get('errors'), list) else []
    display = client.get('display_info') if isinstance(client, dict) else None
    if isinstance(client, dict):
        errors.append({'message':client.get('message')})
    if isinstance(display, dict):
        errors.extend({'message':display.get(key)} for key in ('title','headline','body'))
    diagnostic = save_error_diagnostic({'errors':errors}, {}, '__NO_CARD_TOKEN__')
    typename = client.get('__typename') if isinstance(client, dict) else None
    typename = typename if isinstance(typename, str) and re.fullmatch(r'[A-Za-z_]{1,100}', typename) else 'ABSENT'
    tasks = client.get('next_tasks') if isinstance(client, dict) else None
    task_types = [x.get('__typename') for x in tasks if isinstance(x, dict)
        and isinstance(x.get('__typename'), str) and re.fullmatch(r'[A-Za-z_]{1,100}', x['__typename'])][:10] if isinstance(tasks, list) else []
    logging.getLogger('remask.payment_card').info(
        'card country initialize response account=%s clean=%s type=%s payment_node_match=%s tasks=%s messages=%s',
        target, _clean_payload(result), typename, isinstance(returned, dict)
        and returned.get('id') == evidence['payment_account_node_id'], task_types, diagnostic.get('meta_error_messages', []))
    if (not _clean_payload(result) or not isinstance(client, dict)
            or client.get('__typename') != 'XFBBillableAccountSetCountryCurrencyTimezoneSuccess'
            or client.get('next_tasks') or not isinstance(returned, dict)
            or returned.get('id') != evidence['payment_account_node_id']):
        return {'code':'CARD_COUNTRY_UPDATE_RESULT_UNCONFIRMED', **diagnostic}
    fresh_account = account_proof(await execute(web, 'READ_ACCOUNT', account=target,
        business_id=business_id), target)
    fresh = await execute(web, 'READ_SETUP', payment=payment, business_id=business_id)
    updated = _context(fresh, target, payment)
    if (fresh_account.get('account_scope_verified') is not True
            or fresh_account.get('payment_account_id') != payment
            or fresh_account.get('payment_account_node_id') != evidence['payment_account_node_id']
            or not isinstance(updated, dict) or updated['country'] != desired
            or updated['currency'] != context['currency'] or updated['timezone'] != context['timezone']):
        return {'code':'CARD_COUNTRY_UPDATE_VERIFY_PENDING'}
    return {'code':'CARD_COUNTRY_INITIALIZED_CONFIRMED', 'setup_payload':fresh}


def _context(payload, target, payment):
    if not payment_page_proof(payload, target, payment):
        return None
    account = payload['data']['payment_account']['billable_account']
    tax = account.get('billable_account_tax_info')
    timezone = account.get('timezone_info')
    if not isinstance(tax, dict) or not isinstance(timezone, dict):
        return None
    return {'country':tax.get('business_country_code'), 'currency':account.get('currency'),
            'timezone':timezone.get('timezone'), 'account':account}


async def configure_country(web, *, target, business_id, evidence, current_payload, setup):
    """Return fresh scoped setup, or stop; never acknowledge tax verification."""
    unchanged = {'code':'CARD_COUNTRY_CURRENT_PRESERVED', 'setup_payload':current_payload}
    if not isinstance(setup, dict) or setup.get('country_mode') == 'current':
        return unchanged
    desired = setup.get('country')
    import re
    if (setup.get('country_mode') not in {'strict','prefer_ua'}
            or setup.get('country_mode') == 'prefer_ua' and desired != 'UA'
            or not isinstance(desired, str) or not re.fullmatch(r'[A-Z]{2}', desired)):
        return {'code':'PAYMENT_SETUP_INVALID'}
    payment = evidence['payment_account_id']
    # The existing setup proof is sufficient for no-op country comparisons;
    # timezone/options must be complete before any mutation is attempted.
    if not payment_page_proof(current_payload, target, payment):
        return {'code':'CARD_SETUP_SCOPE_UNVERIFIED'}
    account = current_payload['data']['payment_account']['billable_account']
    tax = account.get('billable_account_tax_info')
    if isinstance(tax, dict) and tax.get('business_country_code') in (None, ''):
        return await _initialize_country(web, target=target, business_id=business_id,
            evidence=evidence, payload=current_payload, desired=desired, setup=setup)
    if not isinstance(tax, dict) or not isinstance(tax.get('business_country_code'), str) or not re.fullmatch(r'[A-Z]{2}', tax['business_country_code']):
        return {'code':'CARD_SETUP_SCOPE_UNVERIFIED'}
    if isinstance(tax, dict) and tax.get('business_country_code') == desired:
        return unchanged
    ownership = methods_proof(await execute(web, 'READ_METHODS', account=target,
        payment=payment, business_id=business_id), target, business_id=business_id, account_evidence=evidence)
    if not all(ownership.get(key) is True for key in ('account_scope_verified','business_scope_verified',
            'payment_account_relation_verified','methods_query_verified')):
        return {'code':'CARD_COUNTRY_UPDATE_SCOPE_UNVERIFIED'}
    web.private_only = True
    decision = await web.graphql(DECISION_DOC, {'paymentAccountID':payment},
        friendly_name='BillingCountryCurrencyDecisionStateQuery', endpoint_url=ENDPOINT,
        business_context_id=business_id)
    data = decision.get('data') if isinstance(decision, dict) else None
    row = data.get('payment_account') if isinstance(data, dict) else None
    node = row.get('billable_account') if isinstance(row, dict) else None
    info = node.get('billable_account_tax_info') if isinstance(node, dict) else None
    if (not _clean_payload(decision) or evidence.get('account_scope_verified') is not True
            or not isinstance(row, dict) or row.get('id') != evidence['payment_account_node_id']
            or not isinstance(node, dict) or node.get('__typename') != 'AdAccount'
            or str(node.get('id','')).removeprefix('act_') != target
            or not isinstance(info, dict) or type(info.get('can_update_tax_country')) is not bool
            or not isinstance(tax, dict) or info.get('business_country_code') != tax.get('business_country_code')
            or node.get('currency') != account.get('currency')):
        return {'code':'CARD_COUNTRY_UPDATE_SCOPE_UNVERIFIED'}
    if not info['can_update_tax_country']:
        return unchanged if setup.get('country_mode') == 'prefer_ua' else {'code':'PAYMENT_COUNTRY_LOCKED'}
    context = _context(current_payload, target, payment)
    options = account.get('supported_country_options')
    if (not isinstance(context, dict) or not isinstance(context['currency'], str)
            or not re.fullmatch(r'[A-Z]{3}', context['currency'])
            or not isinstance(context['timezone'], str) or not context['timezone']
            or not isinstance(options, list)
            or desired not in [option.get('value') for option in options if isinstance(option, dict)]):
        return {'code':'CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED'}
    payload = await web.graphql(UPDATE_DOC, {'input':{
        'billable_account_payment_legacy_account_id':payment, 'country_code':desired,
        'currency':context['currency'], 'timezone':context['timezone']}},
        friendly_name='BillingCountryCurrencyDecisionStateSetCountryCurrencyTimezoneMutation',
        endpoint_url=ENDPOINT, business_context_id=business_id)
    data = payload.get('data') if isinstance(payload, dict) else None
    root = data.get('billable_account_set_country_currency') if isinstance(data, dict) else None
    result = root.get('client_result') if isinstance(root, dict) else None
    returned = root.get('payment_account') if isinstance(root, dict) else None
    if (not _clean_payload(payload) or not isinstance(result, dict)
            or result.get('__typename') != 'XFBBillableAccountSetCountryCurrencyTimezoneSuccess'
            or not isinstance(returned, dict) or returned.get('id') != evidence['payment_account_node_id']
            or returned.get('payment_legacy_account_id') != payment
            or not payment_page_proof({'data':{'payment_account':returned}}, target, payment)):
        return {'code':'CARD_COUNTRY_UPDATE_RESULT_UNCONFIRMED'}
    # The mutation response cannot establish persisted settings or authorize Save.
    fresh_account = account_proof(await execute(web, 'READ_ACCOUNT', account=target,
                                                business_id=business_id), target)
    fresh = await execute(web, 'READ_SETUP', payment=payment, business_id=business_id)
    updated = _context(fresh, target, payment)
    if (fresh_account.get('account_scope_verified') is not True
            or fresh_account.get('payment_account_id') != payment
            or fresh_account.get('payment_account_node_id') != evidence['payment_account_node_id']
            or not isinstance(updated, dict) or updated['country'] != desired
            or updated['currency'] != context['currency'] or updated['timezone'] != context['timezone']):
        return {'code':'CARD_COUNTRY_UPDATE_VERIFY_PENDING'}
    return {'code':'CARD_COUNTRY_UPDATED_CONFIRMED', 'setup_payload':fresh}
