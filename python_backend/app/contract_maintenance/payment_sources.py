"""Explicit read-only maintenance capture, never called by card actions.

Source evidence contains public CDN JS definitions only. A separate sanitized
identity probe may report exact RK/payment-account IDs. Authenticated HTML,
cookies, CSRF, card values and network request bodies are never exported.
This produces source evidence, not an executable or a verified card contract.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
from urllib.parse import urlencode, urlsplit

from ..private_contract_discovery import _module_nodes, _script_urls
from ..private_inventory import _auth_gate
from ..provisioning.models import ProvisioningError

log = logging.getLogger('remask.payment_maintenance')
MAX_SCRIPT_BYTES = 8_000_000
MAX_TOTAL_BYTES = 40_000_000
MAX_EXPORT_BYTES = 8_000_000
REQUIRED_SOURCE_MODULES = (
    'BillingHubPaymentSettingsPaymentMethodsListQuery.graphql',
    'BillingSaveCardCredentialStateMutation.graphql',
    'BillingCountryCurrencyScreenQuery.graphql',
)


def _public_js_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and (p.hostname or '').endswith('.fbcdn.net')
                and not p.username and not p.password and not p.port
                and p.path.endswith('.js') and not p.fragment)
    except (TypeError, ValueError):
        return False


def payment_deferred_script_urls(document):
    """Resolve only literal Bootloader maps supplied in the billing document.

    No guessed CDN addresses, browser execution or module-loader POST. A
    conflicting resource/component definition is never selected arbitrarily.
    """
    resources, components, conflicts = {}, {}, {'rsrcMap': set(), 'compMap': set()}
    decoder = json.JSONDecoder()
    source = html.unescape(document).replace(r'\/', '/')
    for map_name, target in (('rsrcMap', resources), ('compMap', components)):
        for match in re.finditer('"' + map_name + r'"\s*:\s*', source):
            try:
                value, _ = decoder.raw_decode(source, match.end())
            except ValueError:
                continue
            if not isinstance(value, dict):
                continue
            for key, row in value.items():
                if key in target and target[key] != row:
                    conflicts[map_name].add(key)
                else:
                    target[key] = row
    result = []
    for name, component in components.items():
        if name in conflicts['compMap'] or not isinstance(component, dict):
            continue
        if not re.search(r'(?:Billing.*(?:Card|Credential|PaymentMethod|CountryCurrency)|Payment.*(?:Card|Token))', name):
            continue
        ids = component.get('r')
        if not isinstance(ids, list):
            continue
        for identity in ids:
            if not isinstance(identity, (str, int)) or isinstance(identity, bool):
                continue
            identity = str(identity)
            row = resources.get(identity)
            if identity in conflicts['rsrcMap'] or not isinstance(row, dict) or row.get('type') != 'js':
                continue
            url = row.get('src')
            if _public_js_url(url) and url not in result:
                result.append(url)
    return result[:96]


def source_export(rows, *, max_bytes=MAX_EXPORT_BYTES):
    """Keep artifacts and critical senders first; always disclose omissions."""
    def priority(row):
        name = row['name']
        if name.endswith(('.graphql', '_facebookRelayOperation', '$Parameters')):
            return 0
        if any(word in name.lower() for word in ('savecard', 'creditcard', 'token', 'encrypt')):
            return 1
        return 2 if name.endswith('.entrypoint') else 3
    result, used = [], 0
    for row in sorted(rows, key=priority):
        size = len(row['source'].encode())
        if used + size <= max_bytes:
            result.append(row)
            used += size
    names = {row['name'] for row in result}
    return {'modules': result, 'export_bytes': used, 'module_count_total': len(rows),
            'export_truncated': len(result) != len(rows),
            'missing_required_sources': [name for name in REQUIRED_SOURCE_MODULES if name not in names]}


def public_payment_modules(source):
    """Parse CDN source as data; never execute factories or read HTML values."""
    result = []
    for name, factory in _module_nodes(source):
        if not re.fullmatch(r'[A-Za-z0-9_.$-]{1,200}', name):
            continue
        if not any(part in name.lower() for part in ('billing', 'payment', 'creditcard', 'encrypt', 'tokenization')):
            continue
        definition = '__d(' + json.dumps(name) + ',[],' + factory.text.decode() + ');'
        result.append({'name': name, 'sha256': hashlib.sha256(definition.encode()).hexdigest(),
                       'source': definition})
    return result


async def capture_payment_sources(web, *, account_id, business_id):
    if not re.fullmatch(r'\d{5,30}', str(account_id)) or not re.fullmatch(r'\d{5,30}', str(business_id)):
        raise ValueError('INVALID_PAYMENT_TARGET')
    query = urlencode({'asset_id': account_id, 'business_id': business_id})
    entry = 'https://business.facebook.com/billing_hub/payment_settings/?' + query
    status, body, final = await web.fetch_text(entry, max_bytes=3_000_000)
    gate = _auth_gate(final, body)
    if gate:
        raise ProvisioningError(gate, 'Payment contract maintenance requires an authenticated profile.', retryable=True)
    parts = urlsplit(final)
    if status != 200 or parts.hostname != 'business.facebook.com' or 'billing' not in parts.path:
        raise ProvisioningError('PAYMENT_DOCUMENT_UNAVAILABLE', 'Meta did not return a billing document.', retryable=True)
    # Authenticated inline HTML scripts are deliberately NOT returned or parsed
    # for export. Source evidence must come from public static CDN resources.
    # Deferred card/form resources must be fetched before generic UI bundles
    # consume the fixed maintenance budget. Every URL is observed in Meta's
    # document; this path remains completely separate from card execution.
    deferred = payment_deferred_script_urls(body)
    eager = [u for u in _script_urls(body, final, limit=96) if _public_js_url(u)]
    urls = list(dict.fromkeys([*deferred, *eager]))[:96]
    del body
    modules, total, count, errors = {}, 0, 0, 0

    async def read(url):
        try:
            code, source, location = await web.fetch_text(url, max_bytes=MAX_SCRIPT_BYTES + 1, referer=entry)
            if (code != 200 or not _public_js_url(location) or len(source.encode()) > MAX_SCRIPT_BYTES):
                return None
            return source
        except Exception:
            return None

    offset = 0
    while offset < len(urls):
        slots = min(4, (MAX_TOTAL_BYTES - total) // MAX_SCRIPT_BYTES)
        if slots <= 0:
            break
        batch = await asyncio.gather(*(read(url) for url in urls[offset:offset + slots]))
        offset += len(batch)
        for source in batch:
            count += 1
            if source is None:
                errors += 1
                continue
            total += len(source.encode())
            for row in public_payment_modules(source):
                key = (row['name'], row['sha256'])
                if key not in modules:
                    modules[key] = row
    exported = source_export(list(modules.values()))
    log.info('payment source audit profile=%s account=%s scripts=%d bytes=%d modules=%d browser_started=False',
             getattr(getattr(web, 'profile', None), 'name', ''), account_id, count, total, len(modules))
    return {'status': 'SOURCE_EVIDENCE' if modules else 'INCONCLUSIVE',
            'source': 'explicit_payment_contract_maintenance', 'browser_started': False,
            'submitted': False, 'contract_verified': False, 'account_id': account_id,
            'scripts_read': count, 'bytes_read': total, 'script_errors': errors,
            'deferred_scripts_observed': len(deferred), 'scripts_not_read': len(urls) - count,
            **exported}


async def inspect_profile_payment_sources(resolver, profile, target, *, state):
    from ..payment_inspection import account_id, resolve_payment_asset
    from ..session import ProfileSession
    target = account_id(target)
    asset = await resolve_payment_asset(profile, target, state)
    if not asset:
        raise ValueError('PAYMENT_ACCOUNT_BINDING_MISSING')
    context = await resolver.resolve(profile)
    if asset['business_id'] == str(context.cookies.get('c_user') or ''):
        raise ValueError('PERSONAL_AD_ACCOUNT_EXCLUDED')
    async with ProfileSession(context) as session:
        web = await session.facebook_web()
        async with asyncio.timeout(65):
            result = await capture_payment_sources(web, account_id=target, business_id=asset['business_id'])
        # Independent pinned query: a GET document URL is never proof of the
        # payment-account relation. It cannot submit a card. A probe timeout
        # must not discard the already collected public source evidence.
        from ..static_payment_read import execute, account_proof
        try:
            async with asyncio.timeout(15):
                payload = await execute(web, 'READ_ACCOUNT', account=target, business_id=asset['business_id'])
                result['payment_account_probe'] = account_proof(payload, target)
        except Exception:
            result['payment_account_probe'] = {'account_id': target, 'account_scope_verified': False,
                'code': 'PAYMENT_ACCOUNT_QUERY_UNAVAILABLE', 'card_linked': None,
                'funding_verified': False, 'inventory_complete': False}
    return {'profile_id': profile, **result}
