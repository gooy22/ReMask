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
from weakref import WeakValueDictionary

from .ads_sources import deferred_script_urls, public_script_url
from ..private_contract_discovery import _script_urls
from ..private_inventory import _auth_gate
from ..provisioning.models import ProvisioningError
from ..targeting_reference import MODULES as TARGETING_MODULES, refresh_reference
from ..editor_modules import MODULES as EDITOR_MODULES, refresh_editor
from .module_registry import refresh_registry
from .source_observation import oldest_epoch, proof_until
from .module_source_cache import read_script
from .source_invalidation import state as invalidation_state, reject as reject_sources, scope as source_scope

log = logging.getLogger('remask.module_refresh')
BASELINES = Path(__file__).parent.parent / 'contracts' / 'meta_module_refresh_20261013.json'
TTL = 1800
SCRIPT_LIMIT = 24
SCRIPT_BYTES = 2_000_000
TOTAL_BYTES = 12_000_000
URL_LIMIT = 4096
SCRIPT_ATTEMPTS = 3
PROOF_BYTES = 8_000_000
CACHE_PROOF_BYTES = 16_000_000
_cache = OrderedDict()
_collection_locks = WeakValueDictionary()


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
        digest = hashlib.sha256(json.dumps({key:row[key] for key in ('name','dependencies','source')}, sort_keys=True).encode()).hexdigest()
        candidates = versions.setdefault(row['name'], {})
        previous = candidates.get(digest)
        if previous is None or proof_until(row, now, TTL) >= proof_until(previous, now, TTL):
            candidates[digest] = row
    accepted, blocked, incomplete = {}, [], set()
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
        valid_senders = [next(iter(versions[name].values())) for name, digest in spec['senders'].items()
            if len(versions.get(name, {})) == 1 and fingerprint(next(iter(versions[name].values())), doc_id) == digest]
        if valid_artifacts and valid_senders:
            # Retained/cached source never becomes newly observed merely
            # because another collection window finally supplied its sender.
            artifact_until = min(proof_until(next(iter(versions[name].values())), now, TTL) for name in expected)
            sender = max(valid_senders, key=lambda row:(row.get('_source_generation', 0), proof_until(row, now, TTL)))
            sender_until = proof_until(sender, now, TTL)
            accepted[friendly] = {'doc_id': doc_id, 'baseline_doc_id': spec['doc_id'],
                'until': min(artifact_until, sender_until),
                'generation': min([sender.get('_source_generation', 0),
                    *(next(iter(versions[name].values())).get('_source_generation', 0) for name in expected)])}
        elif not valid_artifacts or doc_id != spec['doc_id'] or any(versions.get(name) for name in spec['senders']):
            blocked.append(friendly)
            contradicted = any(versions.get(name) and (len(versions[name]) != 1 or
                fingerprint(next(iter(versions[name].values())), doc_id) != digest)
                for name, digest in expected.items())
            sender_observed = any(versions.get(name) for name in spec['senders'])
            if not contradicted and not (sender_observed and not valid_senders):
                incomplete.add(friendly)
    blocked_generations = {friendly:max((row.get('_source_generation', 0)
        for name in (*references[friendly]['artifact_modules'], *references[friendly]['senders'])
        for row in versions.get(name, {}).values()), default=0) for friendly in blocked}
    return {'accepted': accepted, 'blocked': sorted(blocked), 'observed_modules': len(versions),
        'blocked_generations':blocked_generations, 'invalidated':sorted(set(blocked)-incomplete)}


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
    urls = list(dict.fromkeys(deferred_script_urls(document, preferred=wanted, all_resources=True) +
        [url for url in _script_urls(document, final_url, limit=URL_LIMIT) if public_script_url(url)]))
    if not urls:
        return
    profile = web.profile
    scope = source_scope(web)
    route = str(getattr(profile, 'proxy', '') or '')
    key = hashlib.sha256(json.dumps([getattr(profile, 'name', ''), profile.cookies.get('c_user', ''), route,
        parts.hostname, parts.path, sorted(urls)], separators=(',', ':')).encode()).hexdigest()
    # Concurrent form reads may create separate profile sessions. Serialize
    # the same evidence window so every waiter sees the completed cache entry
    # instead of downloading it again. Scope and source fingerprints are unchanged.
    lock = _collection_locks.setdefault(key, asyncio.Lock())
    async with lock:
        if source_scope(web) != scope:
            raise ProvisioningError('PRIVATE_MODULE_SCOPE_CHANGED', 'Source profile context changed during collection.')
        await _refresh_window(web, final_url, references, wanted, urls, key)


async def _refresh_window(web, final_url, references, wanted, urls, key):
    scope = source_scope(web)
    now = time.monotonic()
    generation = invalidation_state(web)[0]
    previous = _cache.get(key)
    if previous and previous.get('generation', 0) == generation and previous['until'] > now:
        _apply_snapshot(web, previous['snapshot'])
        web._module_collection_progress = dict(previous['progress'])
        web._module_collection_due_at = previous['until']
        return
    # Retain only reviewed proof candidates between bounded windows. A sender,
    # its Relay artifact and a literal reference table may live in different
    # CDN files. Unrelated factories are inventoried, then discarded.
    continuing = previous and previous.get('generation', 0) == generation and not previous['drained']
    state = previous if continuing else {
        'urls': urls[:URL_LIMIT], 'attempts': {}, 'collected': set(),
        'proofs': {}, 'proof_bytes': 0, 'limited': len(urls) > URL_LIMIT,
        'source_observations': {}}
    # Long collections must reacquire expired source rather than rolling the
    # original proof's lifetime forward on every continuation.
    observations = state.setdefault('source_observations', {})
    for url, observed in list(observations.items()):
        if observed + TTL <= now:
            state['collected'].discard(url)
            state['attempts'].pop(url, None)
            del observations[url]
    state['proofs'] = {key: row for key, row in state['proofs'].items() if proof_until(row, now, TTL) > now}
    state['proof_bytes'] = sum(len(row['module_source'].encode()) for row in state['proofs'].values())
    pending = [url for url in state['urls'] if url not in state['collected']
               and state['attempts'].get(url, 0) < SCRIPT_ATTEMPTS]
    window = sorted(pending, key=lambda url: state['attempts'].get(url, 0))[:SCRIPT_LIMIT]
    rows, bytes_read = [], 0
    failures = 0
    cache_stats = {'hits':0,'misses':0}
    # Sequential reads keep a strict aggregate budget and the same cookie jar.
    # The wall clock cap includes parsing and cancellation of the final read.
    try:
        async with asyncio.timeout(12):
            for url in window:
                budget = min(SCRIPT_BYTES, TOTAL_BYTES - bytes_read)
                if budget <= 0:
                    break
                state['attempts'][url] = state['attempts'].get(url, 0) + 1
                try:
                    source = await read_script(web, url, referer=final_url, wanted=wanted, budget=budget, ttl=TTL,
                        stats=cache_stats)
                    size = source['source_bytes']
                    bytes_read += min(size, budget)
                    if not source['valid'] or size > budget:
                        failures += 1
                        continue
                    parsed = source['rows']
                    rows.extend(parsed)
                    state['collected'].add(url)
                    observations[url] = source['observed_at']
                    for candidate in source['deferred_urls']:
                        if candidate in state['urls']:
                            continue
                        if len(state['urls']) >= URL_LIMIT:
                            state['limited'] = True
                            break
                        state['urls'].append(candidate)
                    for row in parsed:
                        if row['name'] not in wanted:
                            continue
                        digest = hashlib.sha256(row['module_source'].encode()).hexdigest()
                        proof_key = (row['name'], digest)
                        if proof_key in state['proofs']:
                            if proof_until(row, time.monotonic(), TTL) > proof_until(state['proofs'][proof_key], time.monotonic(), TTL):
                                state['proofs'][proof_key] = row
                            continue
                        size = len(row['module_source'].encode())
                        if state['proof_bytes'] + size > PROOF_BYTES:
                            state['limited'] = True
                            continue
                        state['proofs'][proof_key] = row
                        state['proof_bytes'] += size
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Do not publish exception messages or connection URLs.
                    bytes_read += budget
                    failures += 1
    except TimeoutError:
        failures += 1
    proofs = list(state['proofs'].values())
    snapshot = select_updates(proofs, references)
    snapshot['scope'] = scope
    if source_scope(web) != scope:
        raise ProvisioningError('PRIVATE_MODULE_SCOPE_CHANGED', 'Source profile context changed during collection.')
    try:
        await asyncio.to_thread(refresh_reference, proofs, observed_at=oldest_epoch(proofs))
    except (ValueError, KeyError, TypeError, OSError):
        # An incomplete or changed reference table cannot erase usable IDs.
        log.info('targeting_reference refresh rejected; installed reference preserved')
    try:
        object_fields=json.loads((BASELINES.parent/'ads_manager_http_20261012.json').read_text())['object_fields']
        await asyncio.to_thread(refresh_editor, rows + proofs, object_fields, observed_at=oldest_epoch(rows + proofs))
    except (ValueError, KeyError, TypeError, OSError):
        log.info('module_metadata refresh rejected; installed adapters preserved')
    # A changed editor table must not suppress collection of unrelated modules.
    try:
        await asyncio.to_thread(refresh_registry, rows, observed_at=oldest_epoch(rows))
    except (ValueError, KeyError, TypeError, OSError):
        log.info('module_inventory refresh rejected; installed inventory preserved')
    _apply_snapshot(web, snapshot)
    pending = [url for url in state['urls'] if url not in state['collected']
               and state['attempts'].get(url, 0) < SCRIPT_ATTEMPTS]
    progress = {'discovered_scripts': len(state['urls']), 'collected_scripts': len(state['collected']),
        'pending_scripts': len(pending), 'failed_scripts': len(state['urls']) - len(state['collected']) - len(pending),
        'collection_limited': state['limited'],
        'collection_complete': not pending and len(state['collected']) == len(state['urls']) and not state['limited']}
    web._module_collection_progress = progress
    until = min([now + (60 if pending else TTL), *(row['until'] for row in snapshot['accepted'].values()),
        *(observed + TTL for observed in observations.values())])
    state.update(until=until, drained=not pending, snapshot=snapshot, progress=progress, generation=generation)
    web._module_collection_due_at = state['until']
    _cache[key] = state
    _cache.move_to_end(key)
    while len(_cache) > 64 or sum(row['proof_bytes'] for row in _cache.values()) > CACHE_PROOF_BYTES:
        _cache.popitem(last=False)
    log.info('module_refresh accepted=%d changed=%d modules=%d failures=%d source_budget_bytes=%d cache_hits=%d cache_misses=%d changed_names=%s http2=True browser_started=False',
        len(snapshot['accepted']), len(snapshot['blocked']), snapshot['observed_modules'], failures, bytes_read,
        cache_stats['hits'], cache_stats['misses'], json.dumps(snapshot['blocked'], separators=(',', ':')))


def _apply_snapshot(web, snapshot):
    scope = source_scope(web)
    if snapshot.get('scope', scope) != scope:
        raise ProvisioningError('PRIVATE_MODULE_SCOPE_CHANGED', 'Source evidence belongs to another profile context.')
    current = getattr(web, '_reviewed_module_ids', {}) if getattr(web, '_reviewed_module_scope', scope) == scope else {}
    # Retain expired IDs as history: learning a new ID must never silently
    # downgrade a later request to the older installed ID after a failed read.
    accepted = dict(current.get('accepted', {}))
    invalidated = snapshot.get('invalidated', snapshot['blocked'])
    _, floors = reject_sources(web, {name:snapshot.get('blocked_generations', {}).get(name, 0)
        for name in invalidated})
    blocked = set(current.get('blocked', [])) | (set(snapshot['blocked'])-set(invalidated))
    for name, floor in floors.items():
        if accepted.get(name, {}).get('generation', 0) < floor:
            accepted.pop(name, None)
            blocked.add(name)
    for name, row in snapshot['accepted'].items():
        if row.get('generation', 0) < floors.get(name, 0):
            continue
        if row['until'] <= time.monotonic():
            if accepted.get(name, {}).get('until', 0) < row['until']:
                accepted[name] = row  # Expired history cannot authorize a baseline fallback.
            continue
        accepted[name] = row
        blocked.discard(name)
    web._reviewed_module_ids = {'accepted': accepted, 'blocked': sorted(blocked)}
    web._reviewed_module_scope = scope


def refresh_due(web):
    now = time.monotonic()
    floors = invalidation_state(web)[1]
    accepted = getattr(web, '_reviewed_module_ids', {}).get('accepted', {})
    progress = getattr(web, '_module_collection_progress', {})
    unfinished = bool(progress) and not progress.get('collection_complete', False)
    return now - getattr(web, '_module_refresh_attempted_at', 0) >= 60 and (
        (bool(accepted) and getattr(web, '_reviewed_module_scope', source_scope(web)) != source_scope(web)) or
        unfinished and now >= getattr(web, '_module_collection_due_at', 0) or any(
            accepted.get(name, {}).get('generation', 0) < floor for name, floor in floors.items()) or any(
            row['until'] <= now for row in getattr(web, '_reviewed_module_ids', {}).get('accepted', {}).values()))


def effective_doc_id(web, friendly_name, original):
    snapshot = getattr(web, '_reviewed_module_ids', {})
    row = snapshot.get('accepted', {}).get(friendly_name)
    floor = invalidation_state(web)[1].get(friendly_name, 0)
    if friendly_name in snapshot.get('blocked', []) or (floor and (row or {}).get('generation', 0) < floor):
        raise ProvisioningError('PRIVATE_MODULE_SCHEMA_CHANGED', 'Reviewed Facebook module changed; no request was submitted.')
    if row and getattr(web, '_reviewed_module_scope', source_scope(web)) != source_scope(web):
        raise ProvisioningError('PRIVATE_MODULE_REFRESH_REQUIRED', 'Module proof belongs to another profile context; no request was submitted.')
    if row and row['until'] <= time.monotonic():
        raise ProvisioningError('PRIVATE_MODULE_REFRESH_REQUIRED', 'Updated Facebook module proof expired; no request was submitted.')
    if row and row['until'] > time.monotonic() and str(original) in {row['baseline_doc_id'], row['doc_id']}:
        return row['doc_id']
    return original
