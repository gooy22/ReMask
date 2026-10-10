"""Pinned verification page read for explicit source maintenance only.

The 2026-10-10 Meta entrypoint uses the default paymentAccountID-only query
variables. It does not run the deferred page component or its task mutations.
No bank parameters, URLs, codes, amounts or response bodies are exported.
"""
import re


async def read_verification_page(web, payment, business):
    if not all(isinstance(v, str) and re.fullmatch(r'\d{5,30}', v) for v in (payment, business)):
        raise ValueError('INVALID_PAYMENT_TARGET')
    web.private_only = True
    return await web.graphql('26638815235726004', {'paymentAccountID': payment},
        friendly_name='BillingThreeDSVerificationPageViewManagerQuery',
        endpoint_url='https://business.facebook.com/api/graphql/', business_context_id=business)


def verification_page_probe(payload, account, payment, node):
    from ..static_payment_read import _clean_payload, _node_id
    result = {'submitted': False, 'account_scope_verified': False, 'field_names': []}
    if not _clean_payload(payload):
        result['code'] = 'VERIFICATION_PAGE_QUERY_REJECTED'
        return result
    data = payload.get('data')
    item = data.get('payment_account') if isinstance(data, dict) else None
    if isinstance(item, dict):
        billable = item.get('billable_account')
        result['account_scope_verified'] = (_node_id(node) and item.get('id') == node
            and item.get('payment_legacy_account_id', payment) == payment
            and (billable is None or (isinstance(billable, dict)
                and billable.get('__typename') == 'AdAccount' and billable.get('id') == account)))
    fields = set()
    def walk(value, path, depth):
        if depth > 6 or len(fields) >= 250:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,100}', key):
                    full = path + '.' + key if path else key
                    fields.add(full)
                    walk(child, full, depth + 1)
        elif isinstance(value, list):
            for child in value[:5]:
                walk(child, path + '[]', depth + 1)
    walk(data, '', 0)
    result['field_names'] = sorted(fields)
    return result
