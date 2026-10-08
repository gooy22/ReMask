"""Versioned Settings contracts. No network discovery, JS parser or browser here.

Operation/schema versions are deployed together. Session auth, identities and
permission task IDs always come from the current profile and fresh read proof.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
import uuid

from .provisioning.models import ProvisioningError

log = logging.getLogger('remask_worker')
MANIFEST = Path(__file__).with_name('contracts') / 'meta_settings_20261008.json'
ENDPOINT = 'https://business.facebook.com/api/graphql/'


@lru_cache(maxsize=1)
def _manifest():
    value = json.loads(MANIFEST.read_text())
    if value.get('version') != 1 or not isinstance(value.get('operations'), dict):
        raise ValueError('Unsupported static contract manifest')
    for op, row in value['operations'].items():
        if (row.get('endpoint_url') != ENDPOINT or not re.fullmatch(r'\d{5,40}', row.get('doc_id', ''))
                or row.get('operation_kind') not in {'query', 'mutation'}
                or not isinstance(row.get('variables'), dict) or not row.get('response_parser')):
            raise ValueError('Invalid static contract: ' + op)
    return value


def contract_metadata(operation):
    manifest = _manifest()
    row = manifest['operations'][operation]
    return {key: row[key] for key in ('doc_id', 'friendly_name', 'operation_kind', 'response_parser', 'evidence')} | {'revision': manifest['revision']}


def command(operation, **bindings):
    row = _manifest()['operations'][operation]
    # Session-free templates can only substitute explicitly named arguments.
    def render(value):
        if isinstance(value, str) and value.startswith('$'):
            name = value[1:]
            if name not in bindings:
                raise ProvisioningError('PRIVATE_STATIC_CONTRACT_ARGUMENT_MISSING', 'Missing static argument: ' + name)
            result = bindings[name]
            if name in {'business', 'page', 'asset', 'user', 'actor'} and not re.fullmatch(r'\d{5,30}', str(result)):
                raise ProvisioningError('PRIVATE_STATIC_CONTRACT_ARGUMENT_INVALID', 'Invalid exact identity: ' + name)
            if name in {'tasks', 'types'} and (not isinstance(result, list) or not result):
                raise ProvisioningError('PRIVATE_STATIC_CONTRACT_ARGUMENT_INVALID', 'Invalid assignment list: ' + name)
            if name == 'tasks' and any(not re.fullmatch(r'\d{5,30}', str(x)) for x in result):
                raise ProvisioningError('PRIVATE_STATIC_CONTRACT_ARGUMENT_INVALID', 'Invalid assigned task ID')
            if name == 'timezone':
                if isinstance(result, bool) or not re.fullmatch(r'\d+', str(result)):
                    raise ProvisioningError('PRIVATE_STATIC_CONTRACT_ARGUMENT_INVALID', 'Invalid timezone ID')
                result = str(result)  # observed Settings sender calls .toString()
            return copy.deepcopy(result)
        if isinstance(value, dict):
            return {key: render(child) for key, child in value.items()}
        if isinstance(value, list):
            return [render(child) for child in value]
        return value
    return {key: row[key] for key in ('doc_id', 'friendly_name', 'endpoint_url', 'operation_kind')} | {'variables': render(row['variables'])}


async def execute(web, operation, *, before_submit=None, business='', variables=None, **bindings):
    if business:
        bindings["business"] = business
    row = command(operation, **bindings)
    if variables is not None:
        if set(variables) != set(row['variables']):
            raise ProvisioningError('PRIVATE_STATIC_CONTRACT_SCHEMA_MISMATCH', 'Static top-level variables changed: ' + operation)
        row['variables'] = copy.deepcopy(variables)
    if row['operation_kind'] == 'mutation' and not callable(before_submit):
        raise ProvisioningError('PRIVATE_STATIC_INTENT_REQUIRED', 'Durable intent is required before ' + operation)
    log.info('[%s] static_contract operation=%s doc_id=%s revision=%s kind=%s browser_started=False',
        getattr(getattr(web, 'profile', None), 'name', ''), operation, row['doc_id'], _manifest()['revision'], row['operation_kind'])
    kwargs = {'friendly_name': row['friendly_name'], 'endpoint_url': row['endpoint_url'], 'business_context_id': business}
    if before_submit is not None:
        kwargs['before_submit'] = before_submit
    submit = getattr(web, 'graphql', None)
    if not callable(submit):
        raise ProvisioningError('CREATE_BM_PRIVATE_TRANSPORT_UNAVAILABLE', 'Direct private HTTP GraphQL transport is unavailable.')
    return await submit(row['doc_id'], row['variables'], **kwargs)


class StaticAssetContracts:
    """Small adapter for the existing durable ownership state machine."""
    def claim_entrypoint(self):
        return command('CLAIM', business='123456789', page='987654321', join='metadata')['variables']['claimingEntryPoint']

    def _command(self, friendly, variables, kind):
        rows = _manifest()['operations']
        selected = [op for op, row in rows.items() if row['friendly_name'] == friendly and row['operation_kind'] == kind]
        if len(selected) != 1:
            return None
        op = selected[0]
        mapping = {'businessID': 'business', 'pageID': 'page', 'assetID': 'asset', 'userID': 'user',
            'qplJoinID': 'join', 'taskIDs': 'tasks', 'assetTypes': 'types'}
        row = command(op, **{mapping[key]: value for key, value in variables.items() if key in mapping})
        # Caller may omit observed defaults but cannot change them or inject keys.
        if not set(variables).issubset(row['variables']) or any(row['variables'][key] != value for key, value in variables.items()):
            return None
        return row

    def query(self, friendly, variables):
        return self._command(friendly, variables, 'query')

    def mutation(self, friendly, bindings):
        return self._command(friendly, bindings, 'mutation')


def rk_create_contract(*, business_id, account_name, currency, timezone_id, actor_id):
    if not str(actor_id).isdigit():
        raise ProvisioningError('SESSION_EXPIRED', 'Current profile actor is unavailable.', retryable=True)
    row = command('CREATE_RK', business=business_id, name=account_name, currency=currency,
                  timezone=timezone_id, join=str(uuid.uuid4()))
    return {**row, 'canary_name': account_name, 'source': 'static_verified_settings_contract',
            'schema_source': 'web_module', 'contract_status': 'pinned', 'request_envelope': {}}


async def create_business_static(web, *, business_name, user_email, user_first_name='', user_last_name='',
                                 profile_display_name='', profile_id='', before_submit=None, manual_doc_id='', **kwargs):
    from .facebook_business_create import _derive_name_parts, CreateBusinessResult, BusinessMutationError
    from .facebook_docids import DocIdCandidate
    row = contract_metadata('CREATE_BM')
    if manual_doc_id and manual_doc_id != row['doc_id']:
        raise ProvisioningError('PRIVATE_STATIC_CONTRACT_OVERRIDE_REJECTED', 'Change the reviewed contract manifest before changing CREATE BM doc_id.')
    bootstrap = await web.bootstrap()
    first, last = _derive_name_parts(display_name=profile_display_name, first_name=user_first_name, last_name=user_last_name)
    result = await execute(web, 'CREATE_BM', actor=str(bootstrap.actor_id), name=business_name,
        first=first, last=last, email=user_email, mutation=uuid.uuid4().hex[:16], join=str(uuid.uuid4()), before_submit=before_submit)
    data = result.get('data') if isinstance(result, dict) else None
    node = data.get('bizkit_create_business') if isinstance(data, dict) else None
    identity = str(node.get('id') or '') if isinstance(node, dict) else ''
    if not re.fullmatch(r'\d{5,30}', identity):
        identity = ''
    path = 'data.bizkit_create_business.id' if identity else ''
    if result.get('errors') or result.get('error') or not identity:
        raise BusinessMutationError('CREATE_BM_RESULT_UNVERIFIED', 'Static BM CREATE response requires reconciliation.', retryable=True, payload=result)
    candidate = DocIdCandidate(operation='CREATE_BM', doc_id=row['doc_id'], friendly_name=row['friendly_name'],
        endpoint_url=ENDPOINT, variables_mode='static_input', source='static_contract', priority=1,
        observed_at='2026-09-24', enabled=True)
    return CreateBusinessResult(identity, candidate, result, path)
