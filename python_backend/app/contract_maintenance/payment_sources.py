"""Explicit read-only maintenance capture, never called by card actions.

Only public CDN JS module definitions are exported. Authenticated HTML, account
data, cookies, CSRF, card values and network request bodies are never exported.
This produces source evidence, not an executable or a verified card contract.
"""
from __future__ import annotations

import asyncio
import hashlib
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
MAX_EXPORT_BYTES = 1_000_000


def public_payment_modules(source):
    """Parse CDN source as data; never execute factories or read HTML values."""
    result = []
    for name, factory in _module_nodes(source):
        if not re.fullmatch(r'[A-Za-z0-9_.$-]{1,200}', name):
            continue
        if not any(part in name.lower() for part in ('billing', 'payment', 'creditcard')):
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
    urls = [u for u in _script_urls(body, final) if (urlsplit(u).hostname or '').endswith('.fbcdn.net')]
    del body
    modules, total, count, errors, exported = {}, 0, 0, 0, 0

    async def read(url):
        try:
            code, source, location = await web.fetch_text(url, max_bytes=MAX_SCRIPT_BYTES + 1, referer=entry)
            p = urlsplit(location)
            if (code != 200 or p.scheme != 'https' or not (p.hostname or '').endswith('.fbcdn.net')
                    or p.username or p.password or p.port or len(source.encode()) > MAX_SCRIPT_BYTES):
                return None
            return source
        except Exception:
            return None

    for offset in range(0, len(urls), 4):
        slots = min(4, (MAX_TOTAL_BYTES - total) // MAX_SCRIPT_BYTES)
        if slots <= 0:
            break
        for source in await asyncio.gather(*(read(url) for url in urls[offset:offset + slots])):
            count += 1
            if source is None:
                errors += 1
                continue
            total += len(source.encode())
            for row in public_payment_modules(source):
                key = (row['name'], row['sha256'])
                size = len(row['source'].encode())
                if key not in modules and exported + size <= MAX_EXPORT_BYTES:
                    modules[key] = row
                    exported += size
    log.info('payment source audit profile=%s account=%s scripts=%d bytes=%d modules=%d browser_started=False',
             getattr(getattr(web, 'profile', None), 'name', ''), account_id, count, total, len(modules))
    return {'status': 'SOURCE_EVIDENCE' if modules else 'INCONCLUSIVE',
            'source': 'explicit_payment_contract_maintenance', 'browser_started': False,
            'submitted': False, 'contract_verified': False, 'account_id': account_id,
            'scripts_read': count, 'bytes_read': total, 'script_errors': errors,
            'modules': list(modules.values())}


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
        async with asyncio.timeout(80):
            result = await capture_payment_sources(web, account_id=target, business_id=asset['business_id'])
    return {'profile_id': profile, **result}
