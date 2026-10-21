"""Bounded public-script evidence, isolated by profile, actor and proxy.

Only parsed public CDN factories/deferred URLs are retained in memory. Private
documents, response bodies and credentials are never stored here. Cache reuse
keeps the original local observation clocks and never renews source freshness.
"""
import asyncio
from collections import OrderedDict
import copy
import hashlib
import json
import time
from weakref import WeakValueDictionary

from .ads_sources import deferred_script_urls, module_records, public_script_url
from ..targeting_reference import MODULES as TARGETING_MODULES

MAX_ENTRIES = 64
MAX_BYTES = 8_000_000  # Serialized parsed-evidence budget; not proxy traffic.
_cache = OrderedDict()
_locks = WeakValueDictionary()


def _scope(web):
    profile = web.profile
    return hashlib.sha256(json.dumps([getattr(profile,'name',''),
        profile.cookies.get('c_user',''), str(getattr(profile,'proxy','') or '')],
        separators=(',', ':')).encode()).hexdigest()


def _copy(entry, *, hit):
    return {**copy.deepcopy(entry), 'cache_hit':hit}


async def read_script(web, url, *, referer, wanted, budget, ttl, stats=None):
    if not public_script_url(url):
        raise ValueError('Public source URL required')
    scope = _scope(web)
    key = hashlib.sha256((scope + '\0' + url).encode()).hexdigest()
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if _scope(web) != scope:
            raise ValueError('Source profile context changed')
        previous = _cache.get(key)
        if previous and previous['observed_at'] + ttl > time.monotonic():
            _cache.move_to_end(key)
            if stats is not None:stats['hits'] += 1
            if previous['source_bytes'] > budget:
                return {'valid':False,'source_bytes':previous['source_bytes'],'cache_hit':True}
            return _copy(previous, hit=True)
        _cache.pop(key, None)
        if stats is not None:stats['misses'] += 1
        status, source, final = await web.fetch_text(url, max_bytes=budget+1, referer=referer)
        size = len(source.encode())
        if status != 200 or size > budget or not public_script_url(final):
            return {'valid':False, 'source_bytes':size, 'cache_hit':False}
        observed, observed_wall = time.monotonic(), time.time()

        def parse():
            script_digest = hashlib.sha256(source.encode()).hexdigest()
            rows = list(module_records(source, selected=wanted, all_modules=True))
            for row in rows:
                row['_source_observed_at'] = observed
                row['_source_observed_wall_at'] = observed_wall
                row['_module_evidence'] = {'module':row['name'],
                    'module_sha256':hashlib.sha256(row['module_source'].encode()).hexdigest(),
                    'script_sha256':script_digest, 'observed_public_url':url}
                if row['name'] in TARGETING_MODULES:
                    row['_targeting_evidence'] = dict(row['_module_evidence'])
            entry = {'valid':True, 'source_bytes':size, 'rows':rows,
                'deferred_urls':deferred_script_urls(source, preferred=wanted, all_resources=True),
                'observed_at':observed}
            entry['cache_bytes'] = len(json.dumps(entry, ensure_ascii=False, separators=(',', ':')).encode())
            return entry

        entry = await asyncio.to_thread(parse)
        if _scope(web) != scope:
            raise ValueError('Source profile context changed')
        if entry['cache_bytes'] <= MAX_BYTES:
            _cache[key] = entry
            _cache.move_to_end(key)
            while len(_cache) > MAX_ENTRIES or sum(row['cache_bytes'] for row in _cache.values()) > MAX_BYTES:
                _cache.popitem(last=False)
        return _copy(entry, hit=False)
