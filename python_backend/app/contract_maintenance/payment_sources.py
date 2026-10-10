"""Explicit read-only maintenance capture, never called by card actions.

Source evidence contains public CDN JS definitions only. A separate sanitized
identity probe may report exact RK/payment-account IDs. Authenticated HTML,
cookies, CSRF, card values and network request bodies are never exported.
This produces source evidence, not an executable or a verified card contract.
"""
from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import html
import json
import logging
import re
import httpx
from urllib.parse import parse_qs, urlencode, urlsplit, urljoin

from ..private_contract_discovery import _module_nodes, _script_urls
from ..private_inventory import _auth_gate
from ..provisioning.models import ProvisioningError

log = logging.getLogger('remask.payment_maintenance')
MAX_SCRIPT_BYTES = 8_000_000
MAX_TOTAL_BYTES = 120_000_000
MAX_EXPORT_BYTES = 8_000_000
MAX_SCRIPTS = 400
# These task module names are anchored to the previously exported Meta
# BillingInit3DS / RiskVerifySDC / NativeOTP Relay page manager families.
# Bootloader GET fetches only static JS, never an auth/charge operation.
OBSERVED_VERIFICATION_VARIANTS = (
    'BillingInit3DSPageViewManager.react',
    'BillingRiskVerifySDCPageViewManager.react',
    'BillingRiskVerifySDCFailedPageViewManager.react',
    'BillingNativeOTPVerificationPageViewManager.react',
)
OPTIONAL_DOCUMENT_TIMEOUT = 12
REQUIRED_SOURCE_MODULES = (
    'BillingHubPaymentSettingsPaymentMethodsListQuery.graphql',
    'BillingAddCreditCardScreenQuery.graphql',
    'BillingAddCreditCardState',
    'BillingSaveCardCredentialStateMutation.graphql',
    'BillingSaveCardCredentialState',
    'BillingCreditCardUtils',
    'BillingCountryCurrencyPageViewManagerQuery.graphql',
    'modularGeneratePTT',
    'getPTTUtils',
    'FBPayAuthLibraryCommon',
    'FBPayAuthLibraryUtils',
)


def source_failure(exc, stage):
    """Classify failures without exporting exception text, URLs or auth data."""
    from ..private_auth import private_auth_error
    from ..session import ProfileContextError
    auth = private_auth_error(exc)
    if auth is not None:
        error = auth
    elif isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        error = ProvisioningError('PAYMENT_CONTRACT_SOURCE_TIMEOUT', 'Payment source capture timed out.', retryable=True)
    elif isinstance(exc, ProfileContextError):
        error = ProvisioningError('PROFILE_CONTEXT_ERROR', 'Profile context is unavailable.', retryable=True)
    elif isinstance(exc, httpx.TransportError) or isinstance(exc.__cause__, (asyncio.TimeoutError, httpx.TransportError)):
        error = ProvisioningError('PAYMENT_SOURCE_NETWORK_UNAVAILABLE', 'Payment source HTTP transport failed.', retryable=True)
    elif isinstance(exc, ProvisioningError):
        error = exc
    else:
        error = ProvisioningError('PAYMENT_CONTRACT_SOURCE_UNAVAILABLE', 'Payment source capture failed.', retryable=True)
    log.warning('payment source failure stage=%s exception_type=%s code=%s', stage, type(exc).__name__, error.code)
    return error


async def payment_source_document(web, entry):
    from ..private_auth import private_auth_error
    # A bounded retry of this read-only GET only. Never retry GraphQL POST,
    # authentication gates or non-network failures; never change proxy/protocol.
    for attempt in range(2):
        try:
            return await asyncio.wait_for(
                web.fetch_text(entry, max_bytes=3_000_000, document_navigation=True), timeout=20)
        except Exception as exc:
            transient = isinstance(exc, (asyncio.TimeoutError, httpx.TransportError)) or isinstance(
                exc.__cause__, (asyncio.TimeoutError, httpx.TransportError))
            if attempt == 0 and transient and private_auth_error(exc) is None and not isinstance(exc, ProvisioningError):
                log.info('payment source document retry reason=network_or_timeout attempt=2')
                continue
            raise source_failure(exc, 'billing_document') from None


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
    def component_priority(name):
        if re.search(r'Risk|Verif|ThreeDS|Init3DS|Authoriz', name):
            return -1
        if re.search(r'SaveCard|AddCreditCard', name):
            return 0
        return 1 if re.search(r'PTT|FBPayAuthLibrary', name) else 2
    result, visited, observed_urls = [], set(), set()
    roots = [name for name in components if re.search(
        r'(?:Billing.*(?:Card|Credential|PaymentMethod|CountryCurrency|PTT|Risk|Verif|ThreeDS|Init3DS|Authoriz)|Payment.*(?:Card|Token|3DS)|FBPay.*|PlatformTrustToken.*|modularGeneratePTT|getPTTUtils)', name)]
    pending = deque(sorted(roots, key=component_priority))
    while pending:
        name = pending.popleft()
        if name in visited:
            continue
        visited.add(name)
        component = components.get(name)
        if name in conflicts['compMap'] or not isinstance(component, dict):
            continue
        # The observed Bootloader reads r, rdfds.r and rds.r, and follows
        # rdfds.m/rds.m module dependencies. Card artifacts can live in a
        # deferred tier even when the root UI has already loaded successfully.
        tiers = [component[key] for key in ('rdfds', 'rds') if isinstance(component.get(key), dict)]
        tiers.append(component)
        for tier in tiers:
            dependency_names = tier.get('m')
            if isinstance(dependency_names, list):
                pending.extend(item for item in dependency_names if isinstance(item, str) and item in components and item not in visited)
        for identity in (item for tier in tiers for item in (tier.get('r') if isinstance(tier.get('r'), list) else [])):
            if not isinstance(identity, (str, int)) or isinstance(identity, bool):
                continue
            identity = str(identity)
            row = resources.get(identity)
            if identity in conflicts['rsrcMap'] or not isinstance(row, dict) or row.get('type') != 'js':
                continue
            url = row.get('src')
            if _public_js_url(url) and url not in observed_urls:
                observed_urls.add(url)
                result.append(url)
    # Discovery must disclose the whole observed inventory. The fetch budget
    # below limits downloads, rather than silently dropping deferred evidence.
    return result


def payment_loader_documents(payload):
    """Use only observed loader maps from query extensions, never card data.

    Some read queries return deferred resource maps in Relay extensions. This
    is source maintenance, not a request-schema search in the normal card path.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('extensions'), dict):
        return []
    pending, documents, visited = [payload['extensions']], [], 0
    while pending and visited < 2000 and len(documents) < 8:
        value = pending.pop(); visited += 1
        if isinstance(value, dict):
            maps = {k: value[k] for k in ('rsrcMap', 'compMap') if isinstance(value.get(k), dict)}
            if maps:
                text = json.dumps(maps)
                if len(text.encode()) <= 1_000_000:
                    documents.append(text)
            pending.extend(v for k, v in value.items() if k not in ('rsrcMap', 'compMap')
                           and isinstance(v, (dict, list, str)))
        elif isinstance(value, list):
            pending.extend(value[:1000])
        elif isinstance(value, str) and len(value.encode()) <= 1_000_000 and ('rsrcMap' in value or 'compMap' in value):
            try:
                decoded = json.loads(value)
            except ValueError:
                # Literal maps inside a loader wrapper are parsed as data by
                # payment_deferred_script_urls. Never execute the wrapper.
                documents.append(value)
            else:
                if isinstance(decoded, (dict, list)):
                    pending.append(decoded)
    return documents


def verification_bootloader_endpoint(document, entry):
    # Read only the observed loader URI. Never export or execute inline HTML.
    source = html.unescape(document)
    values = set()
    for marker in re.finditer(r'"BootloaderEndpointConfig"', source):
        part = source[marker.end():marker.end() + 2000]
        match = re.search(r'"endpointURI"\s*:\s*("(?:[^"\\]|\\.)*")', part)
        if not match:
            continue
        try:
            uri = urljoin(entry, json.loads(match[1]))
            parsed = urlsplit(uri)
            if (parsed.scheme == 'https' and parsed.hostname in
                    {urlsplit(entry).hostname, 'www.facebook.com', 'business.facebook.com', 'adsmanager.facebook.com'}
                    and parsed.path.rstrip('/') == '/ajax/bootloader-endpoint'
                    and not parsed.fragment and not parsed.username and not parsed.password and not parsed.port):
                values.add(uri)
        except (ValueError, TypeError):
            continue
    return next(iter(values)) if len(values) == 1 else None


def verification_lazy_modules(source):
    # Actual literal JSResource/requireDeferred calls only. No guessed names.
    candidates = re.findall(r'(?:JSResourceForInteraction|JSResource|requireDeferred(?:ForDisplay)?)"\)\("([A-Za-z0-9_.$-]{1,200})"\)', source)
    return sorted({name for name in candidates if name.startswith('Billing')
        and re.search(r'Risk|Verif|ThreeDS|Init3DS|NativeOTP|CVCO|Authenticat', name)})


def verification_resource_urls(source):
    """Every observed public JS resource in the requested module GET payload.

    Unlike page maps, this response may omit component roots: the requested
    module is already known. Conflicting resource definitions fail closed.
    """
    decoder = json.JSONDecoder()
    source = html.unescape(source).replace(r'\/', '/')
    rows, conflicts = {}, set()
    for match in re.finditer(r'"rsrcMap"\s*:\s*', source):
        try:
            value, _ = decoder.raw_decode(source, match.end())
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        for key, row in value.items():
            if key in rows and rows[key] != row:
                conflicts.add(key)
            else:
                rows[key] = row
    return list(dict.fromkeys(row['src'] for key, row in rows.items()
        if key not in conflicts and isinstance(row, dict) and row.get('type') == 'js'
        and _public_js_url(row.get('src'))))


def source_export(rows, *, max_bytes=MAX_EXPORT_BYTES):
    """Keep artifacts and critical senders first; always disclose omissions."""
    def priority(row):
        name = row['name']
        if re.search(r'(?:Risk|Verif|ThreeDS|3DS|SDC|CVCO|NativeOTP)', name, re.I):
            return -2
        if name in REQUIRED_SOURCE_MODULES:
            return -1
        if name.endswith(('.graphql', '_facebookRelayOperation', '$Parameters')):
            return 0
        if any(word in name.lower() for word in ('savecard', 'creditcard', 'token', 'encrypt', 'fbpay', 'generateptt', 'getpttutils')):
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
        if not any(part in name.lower() for part in ('billing', 'payment', 'creditcard', 'encrypt', 'tokenization', 'fbpay', 'platformtrusttoken', 'generateptt', 'getpttutils',
                                                    'bootloader', 'jsresource', 'requiredeferred', 'moduleresource', 'haste', 'getasyncparams')):
            continue
        definition = '__d(' + json.dumps(name) + ',[],' + factory.text.decode() + ');'
        result.append({'name': name, 'sha256': hashlib.sha256(definition.encode()).hexdigest(),
                       'source': definition})
    return result


async def capture_payment_sources(web, *, account_id, business_id, loader_documents=(), payment_account_id=None, audit_variant_sources=False):
    if not re.fullmatch(r'\d{5,30}', str(account_id)) or not re.fullmatch(r'\d{5,30}', str(business_id)):
        raise ValueError('INVALID_PAYMENT_TARGET')
    if payment_account_id is not None and not re.fullmatch(r'\d{5,30}', str(payment_account_id)):
        raise ValueError('INVALID_PAYMENT_TARGET')
    query = urlencode({'asset_id': account_id, 'business_id': business_id})
    entry = 'https://business.facebook.com/billing_hub/payment_settings/?' + query
    status, body, final = await payment_source_document(web, entry)
    gate = _auth_gate(final, body)
    if gate:
        raise ProvisioningError(gate, 'Payment contract maintenance requires an authenticated profile.', retryable=True)
    parts = urlsplit(final)
    if status != 200 or parts.hostname != 'business.facebook.com' or 'billing' not in parts.path:
        raise ProvisioningError('PAYMENT_DOCUMENT_UNAVAILABLE', 'Meta did not return a billing document.', retryable=True)
    documents, document_audit = [(body, final)], [{'host': 'business.facebook.com', 'status': 'READ'}]
    if payment_account_id is not None:
        # This is the account-details route observed in the operator's card
        # form. The caller must prove exact RK -> payment account and BM first.
        # Read-only document GET; no wizard task, card save or inline JS runs.
        details = 'https://adsmanager.facebook.com/adsmanager/billing_hub/accounts/details?' + urlencode(
            {'asset_id': payment_account_id, 'business_id': business_id})
        try:
            code, document, location = await asyncio.wait_for(
                payment_source_document(web, details), timeout=OPTIONAL_DOCUMENT_TIMEOUT)
            gate = _auth_gate(location, document)
            destination = urlsplit(location)
            scope = parse_qs(destination.query)
            if gate:
                document_audit.append({'host': 'adsmanager.facebook.com', 'status': 'UNAVAILABLE', 'code': gate})
            elif (code == 200 and destination.hostname == 'adsmanager.facebook.com'
                  and destination.path.rstrip('/') == '/adsmanager/billing_hub/accounts/details'
                  and scope.get('asset_id') == [str(payment_account_id)]
                  and scope.get('business_id') == [str(business_id)]):
                documents.append((document, location))
                document_audit.append({'host': 'adsmanager.facebook.com', 'status': 'READ'})
            else:
                document_audit.append({'host': 'adsmanager.facebook.com', 'status': 'UNAVAILABLE', 'code': 'PAYMENT_DOCUMENT_UNAVAILABLE'})
        except Exception as exc:
            error = source_failure(exc, 'account_details_document')
            document_audit.append({'host': 'adsmanager.facebook.com', 'status': 'UNAVAILABLE', 'code': error.code})
    # Authenticated inline HTML scripts are deliberately NOT returned or parsed
    # for export. Source evidence must come from public static CDN resources.
    # Deferred card/form resources must be fetched before generic UI bundles
    # consume the fixed maintenance budget. Every URL is observed in Meta's
    # document; this path remains completely separate from card execution.
    deferred = payment_deferred_script_urls('\n'.join([*(document for document, _ in documents), *loader_documents]))
    bootloader = next((uri for document, _ in documents
                      if (uri := verification_bootloader_endpoint(document, entry))), None)
    eager = [u for document, location in documents for u in _script_urls(document, location, limit=384) if _public_js_url(u)]
    urls = list(dict.fromkeys([*deferred, *eager]))
    observed = set(urls)
    deferred_observed = set(deferred)
    # Fresh document metadata only. The public getAsyncParams source below
    # must explicitly name a field before this GET can forward it.
    extract = getattr(web, '_extract_request_context', None)
    request_context = extract(body) if callable(extract) else {}
    del body, documents
    modules, total, count, errors = {}, 0, 0, 0
    lazy_attempted, lazy_pending, lazy_requests = set(), set(OBSERVED_VERIFICATION_VARIANTS if audit_variant_sources else ()), 0
    loader_protocol_observed = False
    async_params_source, loader_responses = '', []

    async def read(url):
        try:
            code, source, location = await web.fetch_text(url, max_bytes=MAX_SCRIPT_BYTES + 1, referer=entry)
            if (code != 200 or not _public_js_url(location) or len(source.encode()) > MAX_SCRIPT_BYTES):
                return None
            return source
        except Exception:
            return None

    offset = 0
    while offset < len(urls) and count < MAX_SCRIPTS:
        # This is a read-only maintenance pass, not the normal payment route.
        # Keep a worst-case byte reservation for each concurrent CDN read.
        # More concurrency permits larger inventories inside the existing time cap.
        slots = min(12, MAX_SCRIPTS - count, (MAX_TOTAL_BYTES - total) // MAX_SCRIPT_BYTES)
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
            loader_protocol_observed |= ('__d("BootloaderEndpoint"' in source
                and 'getAsyncParams' in source and '"GET"' in source)
            lazy_pending.update(verification_lazy_modules(source))
            # Public JS may itself carry another literal loader map. Prioritize
            # these observed deferred resources over unrelated eager bundles.
            nested = payment_deferred_script_urls(source)
            deferred_observed.update(nested)
            additions = [url for url in nested if url not in observed]
            observed.update(additions)
            urls[offset:offset] = additions
            for row in public_payment_modules(source):
                if row['name'] == 'getAsyncParams':
                    async_params_source = row['source']
                key = (row['name'], row['sha256'])
                if key not in modules:
                    modules[key] = row
        requested = sorted(lazy_pending - lazy_attempted,
            key=lambda name: (0 if re.search(r'Risk|Verif|ThreeDS|CVCO|SDC|NativeOTP', name, re.I) else 1, name))[:16]
        if bootloader and loader_protocol_observed and requested and lazy_requests < 8:
            # Meta's observed BootloaderEndpoint sender uses GET + modules.
            # Loading source cannot run a verification task or charge a card.
            lazy_attempted.update(requested); lazy_requests += 1
            params = {'modules': ','.join(requested), '__a': '1'}
            params.update({key: value for key, value in request_context.items()
                if isinstance(key, str) and re.fullmatch(r'__[a-z_]{1,30}', key)
                and key in async_params_source and isinstance(value, str) and len(value) <= 20000})
            actor = str(getattr(getattr(web, 'profile', None), 'cookies', {}).get('c_user', ''))
            if re.fullmatch(r'\d{5,30}', actor):
                params['__user'] = actor
            try:
                code, loader, final = await asyncio.wait_for(web.fetch_text(
                    bootloader + ('&' if '?' in bootloader else '?') + urlencode(params), max_bytes=2_000_000, referer=entry), timeout=10)
                response = {'http_status': code, 'resource_count': 0,
                    'rsrc_map_observed': '"rsrcMap"' in loader, 'auth_gate': _auth_gate(final, loader)}
                loader_responses.append(response)
                if code == 200 and urlsplit(final).path.rstrip('/') == '/ajax/bootloader-endpoint':
                    new_urls = verification_resource_urls(loader)
                    response['resource_count'] = len(new_urls)
                    additions = [url for url in new_urls if url not in observed]
                    observed.update(additions); deferred_observed.update(new_urls)
                    urls[offset:offset] = additions
            except Exception:
                loader_responses.append({'code': 'SOURCE_LOADER_REQUEST_UNAVAILABLE'})
    exported = source_export(list(modules.values()))
    # Read only public Relay document definitions. A query doc_id is NOT a
    # financial mutation; logging a candidate is evidence, not authorization.
    verification_ops = []
    for (name, digest), row in modules.items():
        if not (name.endswith('_facebookRelayOperation')
                and re.search(r'Risk|Verif|ThreeDS|SDC|CVCO|NativeOTP', name, re.I)):
            continue
        ids = re.findall(r'exports\s*=\s*"(\d{5,40})"', row['source'])
        if len(ids) == 1:
            verification_ops.append({'name': name, 'doc_id': ids[0],
                'mutation_candidate': 'Mutation' in name})
    verification_ops.sort(key=lambda row: (not row['mutation_candidate'], row['name']))
    if verification_ops:
        log.info('verification deferred Relay candidates profile=%s account=%s operations=%s',
            getattr(getattr(web, 'profile', None), 'name', ''), account_id,
            json.dumps(verification_ops[:60], separators=(',', ':')))
    log.info('verification loader audit profile=%s account=%s attempted=%s responses=%s',
        getattr(getattr(web, 'profile', None), 'name', ''), account_id,
        sorted(lazy_attempted), loader_responses)
    log.info('payment source audit profile=%s account=%s scripts=%d bytes=%d modules=%d browser_started=False',
             getattr(getattr(web, 'profile', None), 'name', ''), account_id, count, total, len(modules))
    return {'status': 'SOURCE_EVIDENCE' if modules else 'INCONCLUSIVE',
            'source': 'explicit_payment_contract_maintenance', 'browser_started': False,
            'submitted': False, 'contract_verified': False, 'account_id': account_id,
            'scripts_read': count, 'bytes_read': total, 'script_errors': errors,
            'deferred_scripts_observed': len(deferred_observed), 'scripts_not_read': len(urls) - count,
            'scripts_observed': len(observed), 'script_limit_reached': count >= MAX_SCRIPTS and offset < len(urls),
            'byte_limit_reached': ((MAX_TOTAL_BYTES - total) // MAX_SCRIPT_BYTES <= 0) and offset < len(urls) and count < MAX_SCRIPTS,
            'document_audit': document_audit,
            'verification_loader_probe': {'endpoint_observed': bool(bootloader),
                'protocol_observed': loader_protocol_observed, 'requests': lazy_requests,
                'modules_requested': sorted(lazy_attempted), 'responses': loader_responses,
                'request_context_keys': sorted(key for key in request_context if key in async_params_source)},
            'verification_relay_operations': verification_ops[:100],
            **exported}


async def inspect_profile_payment_sources(resolver, profile, target, *, state, audit_variant_sources=False):
    try:
        return await _inspect_profile_payment_sources(resolver, profile, target, state=state, audit_variant_sources=audit_variant_sources)
    except ValueError:
        raise
    except Exception as exc:
        raise source_failure(exc, 'maintenance') from None


async def _inspect_profile_payment_sources(resolver, profile, target, *, state, audit_variant_sources=False):
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
        from ..static_payment_read import execute, account_proof, methods_proof, payment_page_proof
        from ..static_payment_card import read_card_screen, card_screen_proof
        evidence = {'account_id': target, 'account_scope_verified': False,
                    'code': 'PAYMENT_ACCOUNT_QUERY_UNAVAILABLE', 'card_linked': None,
                    'funding_verified': False, 'inventory_complete': False}
        methods, options_probe, card_probe, bank_probe, loaders = None, None, None, None, []
        # All four operations are pinned queries. No wizard task, input,
        # tokenization or card save mutation is dispatched by maintenance.
        try:
            async with asyncio.timeout(20):
                # Confirmed saves retain an exact payment scope. Source
                # maintenance must not fail solely on an unrelated hub query.
                from ..payment_card_intents import CardIntentLedger
                intent = await CardIntentLedger(state.path).source_read_intent(profile, target) if state is not None and getattr(state, 'path', None) else None
                saved = json.loads(intent['result']) if intent else {}
                if (saved.get('account_id') == target and saved.get('account_scope_verified') is True
                        and saved.get('business_id') == asset['business_id']
                        and re.fullmatch(r'\d{5,30}', str(saved.get('payment_account_id', '')))
                        and isinstance(saved.get('payment_account_node_id'), str)):
                    evidence = {k: saved[k] for k in ('account_id', 'account_scope_verified',
                        'payment_account_id', 'payment_account_node_id') if k in saved}
                else:
                    payload = await execute(web, 'READ_ACCOUNT', account=target, business_id=asset['business_id'])
                    evidence = account_proof(payload, target)
                if evidence['account_scope_verified'] is True:
                    results = await asyncio.gather(
                        execute(web, 'READ_METHODS', account=target, payment=evidence['payment_account_id'], business_id=asset['business_id']),
                        execute(web, 'READ_VERIFY_OPTIONS', payment=evidence['payment_account_id'], business_id=asset['business_id']),
                        read_card_screen(web, payment=evidence['payment_account_id'], business_id=asset['business_id']),
                        return_exceptions=True)
                    if isinstance(results[0], dict):
                        methods = methods_proof(results[0], target, business_id=asset['business_id'], account_evidence=evidence)
                        loaders = payment_loader_documents(results[0]) if methods.get('business_scope_verified') is True else []
                    if isinstance(results[1], dict):
                        verified = (payment_page_proof(results[1], target, evidence['payment_account_id'])
                            and methods is not None and methods.get('business_scope_verified') is True
                            and methods.get('payment_account_relation_verified') is True)
                        options_probe = {'account_scope_verified': verified, 'submitted': False}
                        if verified:
                            loaders += payment_loader_documents(results[1])
                            options_probe['loader_maps_observed'] = len(loaders)
                    if isinstance(results[2], dict):
                        card_probe = card_screen_proof(results[2], target, account_evidence=evidence)
                        # Form scope proves RK/payment, not BM ownership. Only
                        # accept lazy loader maps when the independent methods
                        # read also proves the selected Business relation.
                        scoped = (card_probe.get('account_scope_verified') is True and methods is not None
                                  and methods.get('methods_query_verified') is True
                                  and methods.get('business_scope_verified') is True
                                  and methods.get('payment_account_relation_verified') is True)
                        card_probe['loader_maps_accepted'] = scoped
                        if scoped:
                            card_loaders = payment_loader_documents(results[2])
                            # Prioritize the actual card screen over generic
                            # payment options when CDN budgets are exhausted.
                            loaders = card_loaders + loaders
                            card_probe['loader_maps_observed'] = len(card_loaders)
                    if (methods is not None and methods.get('account_scope_verified') is True
                            and methods.get('business_scope_verified') is True
                            and methods.get('payment_account_relation_verified') is True):
                        from .payment_verification_source import read_verification_page, verification_page_probe
                        bank_payload = await read_verification_page(web, evidence['payment_account_id'], asset['business_id'])
                        bank_probe = verification_page_probe(bank_payload, target,
                            evidence['payment_account_id'], evidence.get('payment_account_node_id'))
                        if bank_probe['account_scope_verified']:
                            bank_loaders = payment_loader_documents(bank_payload)
                            loaders = bank_loaders + loaders
                            bank_probe['loader_maps_observed'] = len(bank_loaders)
        except Exception:
            # Keep only sanitized completed probes; failure cannot start a
            # browser or discard the public source capture that follows.
            pass
        async with asyncio.timeout(65):
            payment = (evidence.get('payment_account_id') if methods is not None
                       and methods.get('account_scope_verified') is True
                       and methods.get('methods_query_verified') is True
                       and methods.get('business_scope_verified') is True
                       and methods.get('payment_account_relation_verified') is True else None)
            result = await capture_payment_sources(web, account_id=target, business_id=asset['business_id'],
                                                   loader_documents=loaders, payment_account_id=payment,
                                                   audit_variant_sources=audit_variant_sources)
        result['payment_account_probe'] = evidence
        if methods is not None:
            result['payment_methods_probe'] = methods
        if options_probe is not None:
            result['payment_options_probe'] = options_probe
        if card_probe is not None:
            result['payment_card_screen_probe'] = card_probe
        if bank_probe is not None:
            result['verification_page_probe'] = bank_probe
    return {'profile_id': profile, **result}
