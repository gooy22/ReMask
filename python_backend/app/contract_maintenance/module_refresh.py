"""Automatic, profile-bound refresh of reviewed Relay IDs, never JavaScript execution.

An ID is eligible only when its complete artifact AND an observed sender match
the installed source fingerprints. Only the quoted persisted ID is normalized.
Changed code is quarantined before submission; variable templates, response
parsers, payment choices and advertising routes are never rewritten here.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from .ads_sources import deferred_script_urls, module_records, public_script_url
from ..private_contract_discovery import _script_urls
from ..private_inventory import _auth_gate
from ..provisioning.models import ProvisioningError
from ..targeting_reference import MODULES as TARGETING_MODULES, refresh_reference
from ..editor_modules import MODULES as EDITOR_MODULES, refresh_editor
from .module_registry import refresh_registry

log = logging.getLogger('remask.module_refresh')
BASELINES = Path(__file__).parent.parent / 'contracts' / 'meta_module_refresh_20261013.json'
TTL = 1800
SCRIPT_LIMIT = 24
SCRIPT_BYTES = 2_000_000
TOTAL_BYTES = 12_000_000
_cache = OrderedDict()


def fingerprint(row, doc_id):
    # Full factory, including its literal module imports. A changed field,
    # provider, default, selection, parser or sender cannot pass this gate.
    source = re.sub(r'([\"\'])' + re.escape(doc_id) + r'\1', '"@DOC_ID@"', row['source'])
    # Reviewed fixture exports omit the loader dependency array; it is not
    # executable and cannot be inferred. Literal imports remain in the body.
    return hashlib.sha256(source.encode()).hexdigest()


@lru_cache(maxsize=1)
def baselines():
    data = json.loads(BASELINES.read_text())
    if data.get('version') != 1 or data.get('policy') != 'exact_artifact_and_sender_id_only':
        raise ValueError('Module refresh baseline policy unavailable')
    return data['operations']


def select_updates(rows, references, *, now=None):
    """Pure selection: incomplete evidence never authorizes a new ID."""
    now = time.monotonic() if now is None else now
    versions = {}
    for row in rows:
        versions.setdefault(row['name'], {})[hashlib.sha256(json.dumps({key:row[key] for key in ('name','dependencies','source')}, sort_keys=True).encode()).hexdigest()] = row
    accepted, blocked = {}, []
    for friendly, spec in references.items():
        artifact_name = friendly + '.graphql'
        artifacts = list(versions.get(artifact_name, {}).values())
        operation_name = friendly + '_facebookRelayOperation'
        operations = list(versions.get(operation_name, {}).values())
        if not artifacts and not operations:
            continue
        if not artifacts and len(operations) == 1:
            continue
        if len(artifacts) != 1 or len(operations) > 1:
            blocked.append(friendly)
            continue
        id_source = operations[0]['source'] if operations else artifacts[0]['source']
        ids = set(re.findall(r'[\"\'](\d{5,40})[\"\']', id_source))
        if not ids and not operations:
            if fingerprint(artifacts[0], spec['doc_id']) != spec['artifact_modules'][artifact_name]:
                blocked.append(friendly)
            continue
        if len(ids) != 1:
            blocked.append(friendly)
            continue
        doc_id = ids.pop()
        expected = spec['artifact_modules']
        valid_artifacts = all(len(versions.get(name, {})) == 1 and
            fingerprint(next(iter(versions[name].values())), doc_id) == digest
            for name, digest in expected.items())
        # At least one complete captured caller must match. The caller's
        # literal imports and complete body prove the existing payload path.
        valid_sender = any(len(versions.get(name, {})) == 1 and
            fingerprint(next(iter(versions[name].values())), doc_id) == digest
            for name, digest in spec['senders'].items())
        if valid_artifacts and valid_sender:
            accepted[friendly] = {'doc_id': doc_id, 'baseline_doc_id': spec['doc_id'], 'until': now + TTL}
        elif not valid_artifacts or doc_id != spec['doc_id']:
            blocked.append(friendly)
    return {'accepted': accepted, 'blocked': sorted(blocked), 'observed_modules': len(versions)}


async def refresh_from_document(web, document, final_url):
    """Read only CDN resources advertised by this successful authenticated page.

    Called by normal requests after authentication, not on a startup profile
    scan. Uses the same profile HTTP/2 session and proxy; no direct fallback.
    Source code and document/session values are never persisted or logged.
    """
    parts = urlsplit(final_url)
    if (parts.scheme != 'https' or parts.hostname not in {'business.facebook.com', 'www.facebook.com', 'adsmanager.facebook.com'}
            or _auth_gate(final_url, document) or len(document.encode()) > 5_000_000):
        return
    references = baselines()
    wanted = sorted({name for spec in references.values() for name in (*spec['artifact_modules'], *spec['senders'])}
                    | set(TARGETING_MODULES) | set(EDITOR_MODULES))
    urls = list(dict.fromkeys(deferred_script_urls(document, preferred=wanted) +
        [url for url in _script_urls(document, final_url, limit=256) if public_script_url(url)]))[:256]
    if not urls:
        return
    profile = web.profile
    route = str(getattr(profile, 'proxy', '') or '')
    key = hashlib.sha256(json.dumps([getattr(profile, 'name', ''), profile.cookies.get('c_user', ''), route,
        parts.hostname, parts.path, urls], separators=(',', ':')).encode()).hexdigest()
    now = time.monotonic()
    previous = _cache.get(key)
    if previous and previous['until'] > now:
        _apply_snapshot(web, previous['snapshot'])
        return
    cursor = int(previous.get('cursor', 0)) if previous else 0
    window = urls[cursor:cursor + SCRIPT_LIMIT]
    attempted = 0
    rows, bytes_read = [], 0
    failures = 0
    # Sequential reads keep a strict aggregate budget and the same cookie jar.
    # The wall clock cap includes parsing and cancellation of the final read.
    try:
        async with asyncio.timeout(12):
            for url in window:
                budget = min(SCRIPT_BYTES, TOTAL_BYTES - bytes_read)
                if budget <= 0:
                    break
                attempted += 1
                try:
                    status, source, final = await web.fetch_text(url, max_bytes=budget + 1, referer=final_url)
                    size = len(source.encode())
                    bytes_read += min(size, budget)
                    if status != 200 or size > budget or not public_script_url(final):
                        failures += 1
                        continue
                    # Parse bounded public code away from the worker event loop.
                    # Each parser is local; no JavaScript is executed.
                    parsed = await asyncio.to_thread(lambda: [row for row in
                        module_records(source, selected=wanted, all_modules=True)])
                    for row in parsed:
                        row['_module_evidence'] = {'module':row['name'],
                            'module_sha256':hashlib.sha256(row['module_source'].encode()).hexdigest(),
                            'script_sha256':hashlib.sha256(source.encode()).hexdigest(),'observed_public_url':url}
                        if row['name'] in TARGETING_MODULES:
                            row['_targeting_evidence'] = {'module':row['name'],
                                'module_sha256':hashlib.sha256(row['module_source'].encode()).hexdigest(),
                                'script_sha256':hashlib.sha256(source.encode()).hexdigest(),
                                'observed_public_url':url}
                    rows.extend(parsed)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Do not publish exception messages or connection URLs.
                    bytes_read += budget
                    failures += 1
    except TimeoutError:
        failures += 1
    snapshot = select_updates(rows, references)
    try:
        await asyncio.to_thread(refresh_reference, rows)
    except (ValueError, KeyError, TypeError, OSError):
        # An incomplete or changed reference table cannot erase usable IDs.
        log.info('targeting_reference refresh rejected; installed reference preserved')
    try:
        object_fields=json.loads((BASELINES.parent/'ads_manager_http_20261012.json').read_text())['object_fields']
        await asyncio.to_thread(refresh_editor, rows, object_fields)
        await asyncio.to_thread(refresh_registry, rows)
    except (ValueError, KeyError, TypeError, OSError):
        log.info('module_metadata refresh rejected; installed adapters preserved')
    _apply_snapshot(web, snapshot)
    cursor += attempted
    complete = cursor >= len(urls)
    _cache[key] = {'until': now + (60 if failures or not complete else TTL),
                   'cursor': 0 if complete else cursor, 'snapshot': snapshot}
    _cache.move_to_end(key)
    while len(_cache) > 64:
        _cache.popitem(last=False)
    log.info('module_refresh accepted=%d changed=%d modules=%d failures=%d bytes=%d changed_names=%s http2=True browser_started=False',
        len(snapshot['accepted']), len(snapshot['blocked']), snapshot['observed_modules'], failures, bytes_read,
        json.dumps(snapshot['blocked'], separators=(',', ':')))


def _apply_snapshot(web, snapshot):
    current = getattr(web, '_reviewed_module_ids', {})
    # Retain expired IDs as history: learning a new ID must never silently
    # downgrade a later request to the older installed ID after a failed read.
    accepted = dict(current.get('accepted', {}))
    blocked = set(current.get('blocked', [])) | set(snapshot['blocked'])
    for name in snapshot['blocked']:
        accepted.pop(name, None)
    for name, row in snapshot['accepted'].items():
        accepted[name] = row
        blocked.discard(name)
    web._reviewed_module_ids = {'accepted': accepted, 'blocked': sorted(blocked)}


def refresh_due(web):
    now = time.monotonic()
    return now - getattr(web, '_module_refresh_attempted_at', 0) >= 60 and any(
        row['until'] <= now for row in getattr(web, '_reviewed_module_ids', {}).get('accepted', {}).values())


def effective_doc_id(web, friendly_name, original):
    snapshot = getattr(web, '_reviewed_module_ids', {})
    if friendly_name in snapshot.get('blocked', []):
        raise ProvisioningError('PRIVATE_MODULE_SCHEMA_CHANGED', 'Reviewed Facebook module changed; no request was submitted.')
    row = snapshot.get('accepted', {}).get(friendly_name)
    if row and row['until'] <= time.monotonic() and row['doc_id'] != row['baseline_doc_id']:
        raise ProvisioningError('PRIVATE_MODULE_REFRESH_REQUIRED', 'Updated Facebook module proof expired; no request was submitted.')
    if row and row['until'] > time.monotonic() and str(original) in {row['baseline_doc_id'], row['doc_id']}:
        return row['doc_id']
    return original
