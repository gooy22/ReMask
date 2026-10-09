"""Apply an explicitly selected billing country to the existing exact RK only.

Current BillingCountryCurrencyDecisionState uses a separate mutation for an
UPDATE; closing the old account and creating another is deliberately unsupported.
Currency/timezone are retained, never replaced by defaults during card binding.
"""
from .static_payment_read import ENDPOINT, _clean_payload, execute, account_proof, payment_page_proof, methods_proof

DECISION_DOC = '28210215908604615'
UPDATE_DOC = '29520642304190454'


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
