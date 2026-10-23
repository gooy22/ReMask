"""Operation-local HTTP observations, not TLS wire bytes or proxy billing.

HTTPX counts encoded response body bytes consumed from its response stream.
Decoded bytes returned by our body reader are counted separately. Aggregates
and logs contain no request body, URL, headers, cookie, actor or proxy value.
Bounded response references exist only until the request scope finishes.
Errors describe exceptions leaving that scope; 'other' is not proof of a
transport failure. Decoded chunks never returned to the caller are excluded.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import wraps
import asyncio
import json
import logging
import secrets
import time

import httpx

log = logging.getLogger('remask_worker')
_RESOURCE_NAMES = frozenset({'interests', 'behaviors', 'locations', 'locales',
    'pages', 'images', 'videos', 'campaigns', 'adsets', 'ads', 'pixels',
    'custom_audiences', 'conversion_goals', 'instagram_accounts',
    'instagram_media', 'creatives', 'insights', 'college_years',
    'industries', 'life_events', 'family_statuses', 'user_device',
    'user_os'})
_OPERATIONS = frozenset({'private_launch', 'private_delivery', 'provision', 'provisioning',
    'prepare', 'prepare_profile', 'proxy_check', 'transparent_post', 'sync_profile',
    'template_reference', 'resource_other'} | {'resource_' + name for name in _RESOURCE_NAMES})
MAX_RESPONSE_REFERENCES = 512
_PHASES = frozenset({'resolver', 'cache_lookup', 'profile_session', 'reference_discovery',
    'reference_read', 'ads_document', 'account_proof', 'meta_read', 'inventory_read',
    'resource_read', 'queue_wait', 'profile_lock', 'store_read', 'store_write'})
_ERRORS = (httpx.TimeoutException, TimeoutError, httpx.ProxyError,
    httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.ProtocolError,
    httpx.DecodingError, httpx.TooManyRedirects)
_current_operation: ContextVar[OperationTraffic | None] = ContextVar('private_http_operation', default=None)
_current_request: ContextVar[RequestTraffic | None] = ContextVar('private_http_request', default=None)


def error_kind(error):
    # Never serialize an exception message (it can contain proxy credentials).
    if error is None:
        return 'none'
    if isinstance(error, asyncio.CancelledError):
        return 'cancelled'
    for kind in _ERRORS:
        if isinstance(error, kind):
            return 'timeout' if kind in (httpx.TimeoutException, TimeoutError) else kind.__name__
    return 'other'


@dataclass
class OperationTraffic:
    profile_id: str
    operation: str
    started: float = field(default_factory=time.monotonic)
    trace_id: str = field(default_factory=lambda: secrets.token_hex(6))
    active: bool = True
    phase_ms: Counter = field(default_factory=Counter)
    phase_count: Counter = field(default_factory=Counter)
    request_scopes: int = 0
    completed_request_scopes: int = 0
    request_attempts: int = 0
    responses: int = 0
    redirects: int = 0
    rate_limit_responses: int = 0
    untracked_responses: int = 0
    measurement_errors: int = 0
    profile_scope_mismatches: int = 0
    redirect_decoded_bodies_unavailable: int = 0
    encoded_response_body_bytes: int = 0
    decoded_body_bytes_delivered: int = 0
    decoded_redirect_body_bytes: int = 0
    request_scope_ms: float = 0
    status_classes: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)

    def snapshot(self, *, outcome='observed'):
        return {'operation': self.operation, 'outcome': outcome,
            'trace_id': self.trace_id,
            'phase_ms': {k:round(v, 3) for k,v in sorted(self.phase_ms.items())},
            'phase_count': dict(sorted(self.phase_count.items())),
            'operation_wall_ms': round((time.monotonic() - self.started) * 1000, 3),
            'request_scopes': self.request_scopes,
            'completed_request_scopes': self.completed_request_scopes,
            'request_attempts': self.request_attempts, 'responses': self.responses,
            'redirect_responses': self.redirects,
            'rate_limit_responses': self.rate_limit_responses,
            'untracked_responses': self.untracked_responses,
            'measurement_errors': self.measurement_errors,
            'profile_scope_mismatches': self.profile_scope_mismatches,
            'encoded_response_body_bytes': self.encoded_response_body_bytes,
            'decoded_body_bytes_delivered': self.decoded_body_bytes_delivered,
            'decoded_redirect_body_bytes': self.decoded_redirect_body_bytes,
            'redirect_decoded_bodies_unavailable': self.redirect_decoded_bodies_unavailable,
            'request_scope_ms': round(self.request_scope_ms, 3),
            'status_classes': dict(sorted(self.status_classes.items())),
            'errors': dict(sorted(self.errors.items())),
            'complete': self.request_scopes == self.completed_request_scopes and not (
                self.untracked_responses or self.measurement_errors or self.profile_scope_mismatches
                or self.redirect_decoded_bodies_unavailable),
            'byte_scope': 'http_response_body_not_tls_headers_or_proxy_billing',
            'transport_coverage': 'PrivateHttpClient_only',
            'request_body_bytes_measured': False}


@contextmanager
def measure_phase(name):
    """Measure an allowlisted wall-time phase within a currently active scope.

    Does not capture profile credentials, endpoint URLs, request fields, or
    arbitrary labels. Nested/concurrent phase durations may overlap; they
    must not be summed to infer the total operation wall-time.
    """
    operation = _current_operation.get()
    enabled = (name in _PHASES and operation is not None and operation.active)
    started = time.monotonic() if enabled else None
    try:
        yield
    finally:
        if (enabled and operation.active and
                _current_operation.get() is operation):
            operation.phase_ms[name] += (time.monotonic() - started) * 1000
            operation.phase_count[name] += 1


def begin_operation(profile_id, action):
    metric = OperationTraffic(str(profile_id), action if action in _OPERATIONS else 'other')
    return metric, _current_operation.set(metric)


def end_operation(metric, token: Token, *, outcome):
    try:
        _current_operation.reset(token)
    except (ValueError, RuntimeError):
        _current_operation.set(None)
    metric.active = False
    # The only identifier is the existing worker profile ID. Operation names
    # and outcomes are allowlisted rather than arbitrary task payload strings.
    safe_outcome = outcome if outcome in {'success', 'partial', 'failed', 'cancelled'} else 'other'
    try:
        log.info('[%s] private_http_operation %s', metric.profile_id,
            json.dumps(metric.snapshot(outcome=safe_outcome), sort_keys=True, separators=(',', ':')))
    except Exception:
        # Observability must never turn a confirmed Meta mutation into a job
        # failure (and thereby encourage an operator to submit it again).
        pass



def observe_profile_read(function):
    """Account for direct resource/reference HTTP reads without logging a query.

    The allowlisted resource kind helps separate heavy HTML bootstrap from the
    actual small response. Only PrivateHttpClient body bytes are measured; proxy
    billing and non-private transports remain explicitly out of scope.
    """
    @wraps(function)
    async def wrapped(*args, **kwargs):
        profile = str(kwargs.get('profile_id', args[0] if args else '') or '').strip()
        resource = kwargs.get('resource')
        name = ('resource_' + resource if type(resource) is str and
                resource in _RESOURCE_NAMES else
                'resource_other' if resource is not None else 'template_reference')
        existing = _current_operation.get()
        if existing is not None and existing.active and existing.profile_id == profile:
            return await function(*args, **kwargs)
        metric, token = begin_operation(profile, name)
        outcome = 'failed'
        try:
            result = await function(*args, **kwargs)
            outcome = 'success'
            return result
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        finally:
            end_operation(metric, token, outcome=outcome)
    return wrapped


def observe_profile_inventory(function):
    """Scope only the direct live-inventory endpoint, preserving its signature.

    Internal calls from an already scoped task of this profile contribute to
    that existing operation instead of emitting overlapping traffic totals.
    """
    @wraps(function)
    async def wrapped(*args, **kwargs):
        profile = str(kwargs.get('profile_id', args[0] if args else '') or '').strip()
        existing = _current_operation.get()
        if existing is not None and existing.active and existing.profile_id == profile:
            return await function(*args, **kwargs)
        metric, token = begin_operation(profile, 'sync_profile')
        outcome = 'failed'
        try:
            result = await function(*args, **kwargs)
            outcome = 'partial' if isinstance(result, dict) and result.get('live_ready') is False else 'success'
            return result
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        finally:
            end_operation(metric, token, outcome=outcome)
    return wrapped


class RequestTraffic:
    def __init__(self, operation):
        self.operation = operation
        self.started = time.monotonic()
        self.attempts = 0
        self.responses = []
        self.untracked = 0
        self.delivered = 0
        self.operation.request_scopes += 1

    def response(self, response):
        if not any(item is response for item in self.responses):
            if len(self.responses) < MAX_RESPONSE_REFERENCES:
                self.responses.append(response)
            else:
                self.untracked += 1

    def finish(self, error):
        metric = self.operation
        if not metric.active:
            # A watchdog-detached request must not contaminate a later task.
            # Its original operation already reports an incomplete scope.
            return
        metric.completed_request_scopes += 1
        metric.request_attempts += self.attempts
        metric.responses += len(self.responses) + self.untracked
        metric.untracked_responses += self.untracked
        metric.decoded_body_bytes_delivered += self.delivered
        metric.request_scope_ms += (time.monotonic() - self.started) * 1000
        if error is not None:
            metric.errors[error_kind(error)] += 1
        for response in self.responses:
            metric.encoded_response_body_bytes += response.num_bytes_downloaded
            status = response.status_code
            if status == 429:
                metric.rate_limit_responses += 1
            metric.status_classes[str(status // 100) + 'xx' if 100 <= status <= 599 else 'other'] += 1
            if response.is_redirect:
                metric.redirects += 1
                # HTTPX already consumes redirect bodies. Access its existing
                # content only; never read/retain another copy of the stream.
                try:
                    metric.decoded_redirect_body_bytes += len(response.content)
                except httpx.ResponseNotRead:
                    if response.num_bytes_downloaded:
                        metric.redirect_decoded_bodies_unavailable += 1


def begin_request(profile_id):
    operation = _current_operation.get()
    if operation is not None and operation.active and operation.profile_id != str(profile_id):
        operation.profile_scope_mismatches += 1
    metric = RequestTraffic(operation) if (operation is not None and operation.active
        and operation.profile_id == str(profile_id)) else None
    return metric, _current_request.set(metric)


def end_request(metric, token: Token, error):
    try:
        _current_request.reset(token)
    except (ValueError, RuntimeError):
        _current_request.set(None)
    if metric is not None:
        try:
            metric.finish(error)
        except Exception:
            if metric.operation.active:
                metric.operation.measurement_errors += 1
        finally:
            metric.responses.clear()


async def request_hook(request):
    metric = _current_request.get()
    if metric is not None:
        metric.attempts += 1


async def response_hook(response):
    metric = _current_request.get()
    if metric is not None:
        try:
            metric.response(response)
        except Exception:
            if metric.operation.active:
                metric.operation.measurement_errors += 1
