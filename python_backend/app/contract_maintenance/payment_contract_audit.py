"""Maintenance-only public query metadata. No source or runtime data export.

This cannot execute a compiled operation or enable card Save. It reports only
persisted query IDs, variable names and response field names for review.
"""
from __future__ import annotations

import hashlib
import re

from ..private_contract_discovery import _module_nodes, _walk, _pairs, _export_contains
from ..private_inventory_queries import QueryArtifacts

_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,159}\Z')
_RELEVANT = re.compile(r'(?:Country|Tax|BinProperties)', re.I)


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError('INVALID_PUBLIC_QUERY_METADATA')
    return value


def _fields(selections, prefix='', depth=0):
    if not isinstance(selections, list) or len(selections) > 300 or depth > 16:
        raise ValueError('INVALID_PUBLIC_QUERY_METADATA')
    result = []
    for item in selections:
        if not isinstance(item, dict):
            raise ValueError('INVALID_PUBLIC_QUERY_METADATA')
        kind = item.get('kind')
        if kind in ('ScalarField', 'LinkedField'):
            path = prefix + _name(item.get('name'))
            row = {'path': path, 'kind': kind}
            if item.get('concreteType') is not None:
                row['concrete_type'] = _name(item['concreteType'])
            args = item.get('args') or []
            if not isinstance(args, list) or len(args) > 40:
                raise ValueError('INVALID_PUBLIC_QUERY_METADATA')
            # Literal values and defaults are intentionally never exported.
            row['arguments'] = [{'name': _name(arg['name']), 'kind': _name(arg['kind']),
                **({'variable': _name(arg['variableName'])} if arg.get('kind') == 'Variable' else {})}
                for arg in args if isinstance(arg, dict)]
            if len(row['arguments']) != len(args):
                raise ValueError('INVALID_PUBLIC_QUERY_METADATA')
            result.append(row)
            if kind == 'LinkedField':
                result.extend(_fields(item.get('selections'), path + '.', depth + 1))
        elif kind in ('InlineFragment', 'Condition', 'Defer', 'Stream', 'ClientExtension'):
            result.extend(_fields(item.get('selections'), prefix, depth + 1))
        elif kind not in ('TypeDiscriminator', 'FragmentSpread'):
            raise ValueError('INVALID_PUBLIC_QUERY_METADATA')
    return result


def public_country_query_audit(snapshot):
    rows = snapshot.get('modules') if isinstance(snapshot, dict) else None
    if not isinstance(rows, list):
        rows = []
    metadata = QueryArtifacts()
    # Only CDN factories from the existing private capture are parsed as data.
    # Raw source stays within this function and is never included in its result.
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get('source'), str):
            for name, module in _module_nodes(row['source']):
                if _RELEVANT.search(name) or name.endswith('_facebookRelayOperation'):
                    metadata.modules.setdefault(name, {})[hashlib.sha256(module.text).hexdigest()] = module
    present = sorted(name for name in metadata.modules if _RELEVANT.search(name))
    queries = []
    for module_name in present:
        versions = metadata.modules[module_name]
        if not module_name.endswith('.graphql') or len(versions) != 1:
            continue
        module = next(iter(versions.values()))
        candidates = []
        for node in _walk(module):
            if node.type != 'object':
                continue
            try:
                pairs = _pairs(node)
                if not {'params', 'operation'}.issubset(pairs) or not _export_contains(metadata, module, node):
                    continue
                params, operation = metadata._read(pairs['params']), metadata._read(pairs['operation'])
                friendly = _name(params.get('name'))
                doc = params.get('id')
                if (params.get('operationKind') != 'query' or operation.get('kind') != 'Operation'
                        or operation.get('name') != friendly or module_name != friendly + '.graphql'
                        or not isinstance(doc, str) or not re.fullmatch(r'\d{5,40}', doc)):
                    continue
                args = operation.get('argumentDefinitions')
                if not isinstance(args, list) or len(args) > 40:
                    continue
                candidate = {'friendly_name': friendly, 'doc_id': doc,
                    'artifact_sha256': hashlib.sha256(module.text).hexdigest(),
                    'variables': sorted(_name(a['name']) for a in args),
                    'fields': _fields(operation.get('selections'))}
                candidates.append(candidate)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        if len(candidates) == 1:
            queries.append(candidates[0])
    return {'status': 'PUBLIC_QUERY_AUDIT', 'source': 'payment_contract_metadata_audit',
        'submitted': False, 'browser_started': False, 'execution_enabled': False,
        'modules_present': present, 'queries': queries,
        'country_utility_present': 'BillingCountryVerificationUtils' in present,
        'capture_complete': snapshot.get('scripts_not_read') == 0 and not snapshot.get('export_truncated')}


async def inspect_payment_contract_audit(resolver, profile, target, *, state):
    from .payment_sources import inspect_profile_payment_sources
    snapshot = await inspect_profile_payment_sources(resolver, profile, target, state=state)
    result = public_country_query_audit(snapshot)
    # No account probe, card mask, literal/default value, URL, source definition,
    # cookies, token or request body crosses this boundary.
    return {'profile_id': profile, 'account_id': target, **result}
