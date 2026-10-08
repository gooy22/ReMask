"""Read-only static BM/RK inventory; never sends a mutation or starts a browser."""
from __future__ import annotations

import json
import logging
import re

from .static_meta_contracts import execute, command, contract_metadata
from .private_inventory import (_business_payloads, _extract_inventory_ad_account_rows,
    _normalize_connected_inventory, _scoped_collection_observations)
from .provisioning.models import ProvisioningError

log = logging.getLogger('remask_worker')


def identity(value):
    text = str(value or '').removeprefix('act_')
    return text if re.fullmatch(r'\d{5,30}', text) else ''


def diagnostic(operation, payload):
    """Paths/types/counts only, with a finite budget. No response scalar values."""
    paths = []
    pending = [('data', payload.get('data'))] if isinstance(payload, dict) else []
    while pending and len(paths) < 35:
        path, value = pending.pop(0)
        if path.count('.') > 7:
            continue
        if isinstance(value, dict):
            paths.append({'path': path, 'fields': sorted(key for key in value if not re.search(r'token|auth|cookie', key, re.I))[:20]})
            pending.extend((path+'.'+key, child) for key, child in value.items()
                if isinstance(child, (dict, list)) and not re.search(r'token|auth|cookie', key, re.I))
        elif isinstance(value, list):
            paths.append({'path': path, 'count': len(value)})
            pending.extend((path+'[]', child) for child in value[:1] if isinstance(child, (dict, list)))
    return {'operation': operation, **contract_metadata(operation), 'has_errors': bool(isinstance(payload, dict) and (payload.get('errors') or payload.get('error'))), 'response_shape': paths}


def _bm_scopes(payload):
    if not isinstance(payload, dict) or payload.get('errors') or payload.get('error'):
        return {}, False, False
    data = payload.get('data')
    viewer = data.get('viewer') if isinstance(data, dict) else None
    scoping = viewer.get('meta_business_scoping') if isinstance(viewer, dict) else None
    connection = scoping.get('business_scopes') if isinstance(scoping, dict) else None
    if not isinstance(connection, dict):
        return {}, False, False
    # This operation requests the complete first-level scope prefix, with no
    # name, status, asset or selected-scope filter. Never count incidental BM nodes.
    nodes = connection.get('nodes')
    if nodes is None and isinstance(connection.get('edges'), list):
        nodes = [edge.get('node') if isinstance(edge, dict) else None for edge in connection['edges']]
    if not isinstance(nodes, list):
        return {}, False, False
    rows = {}
    for node in nodes:
        if not isinstance(node, dict) or not identity(node.get('scope_id')) or not isinstance(node.get('scope_type'), str):
            return {}, False, False
        scope = node['scope_type'].upper()
        if scope in {'PERSONAL', 'INSTAGRAM_BUSINESS_ASSET'}:
            continue
        if scope != 'BUSINESS' or not isinstance(node.get('scope_name'), str):
            return {}, False, False
        key = identity(node['scope_id'])
        if key in rows:
            return {}, False, False
        rows[key] = node['scope_name']
    page = connection.get('page_info', connection.get('pageInfo'))
    next_page = page.get('has_next_page', page.get('hasNextPage')) if isinstance(page, dict) else None
    previous = page.get('has_previous_page', page.get('hasPreviousPage', False)) if isinstance(page, dict) else None
    complete = next_page is False and previous is False
    # A typed explicit total confirms a complete prefix too; a short list alone
    # never proves absence. The response contract remains marked unverified
    # until its actual Meta shape has been recorded independently.
    total = connection.get('total_count')
    if type(total) is int and total >= 0 and total == len(nodes) and next_page is not True:
        complete = True
    return rows, complete, next_page is True


async def read_business_inventory(web, expected=''):
    rows, complete, diagnostics = {}, False, []
    if expected:
        payload = await execute(web, 'CONFIG', business=expected)
        diag = diagnostic('CONFIG', payload)
        diagnostics.append(diag)
        data = payload.get('data') if isinstance(payload, dict) else None
        node = data.get('business') if isinstance(data, dict) else None
        if (not diag['has_errors'] and isinstance(node, dict) and identity(node.get('id')) == expected
                and node.get('scheduledForDeletion') is not True and isinstance(node.get('name'), str)):
            return {'rows': {expected: node['name']}, 'complete': False,
                    'source': 'static_http_exact_business_config', 'diagnostics': diagnostics}
    variables = command('READ_BM')['variables']
    for count in (200, 400, 800):
        variables['fetchNumberForBusinessScopes'] = count
        payload = await execute(web, 'READ_BM', variables=variables)
        found, complete, more = _bm_scopes(payload)
        rows.update(found)
        diag = diagnostic('READ_BM', payload)
        diag.update(business_ids=sorted(found), complete=complete, requested_count=count)
        diagnostics.append(diag)
        log.info('BM static inventory expected=%s verification=%s', expected, json.dumps(diag, separators=(',', ':')))
        if complete or not more or expected in rows:
            break
    return {'rows': rows, 'complete': complete, 'source': 'static_http_business_scopes', 'diagnostics': diagnostics}


async def read_ad_account_inventory(web, business, name, expected=''):
    payload = await execute(web, 'READ_RK', business=business)
    diag = diagnostic('READ_RK', payload)
    normalized = _normalize_connected_inventory([payload], business)
    observations, accounts = [], {}
    if not isinstance(payload, dict) or payload.get('errors') or payload.get('error'):
        normalized = []
    for result in normalized:
        for node in _business_payloads(result, business):
            observations.extend(_scoped_collection_observations(node, True))
            for account in _extract_inventory_ad_account_rows(node, request_scoped=True):
                key = identity(account.get('id'))
                if key and identity(account.get('business_id')) == business:
                    accounts[key] = account
    ids = sorted(accounts)
    named = sorted(key for key, row in accounts.items() if str(row.get('name') or '').strip().casefold() == name.casefold())
    complete = bool(observations) and all(observations)
    selected = expected if expected and expected in ids else (named[0] if not expected and complete and len(named) == 1 else '')
    diag.update(business_id=business, account_ids=ids, complete=complete)
    log.info('RK static inventory verification=%s', json.dumps(diag, separators=(',', ':')))
    return {'id': selected, 'ids': ids, 'named': named, 'complete': complete,
        'asset_ui_id': identity(accounts.get(selected, {}).get('business_object_ui_id')),
        'source': 'static_http_exact_business_inventory', 'diagnostics': [diag]}
