"""Pinned Business Suite Page contracts; discovery is an offline maintenance task."""
from __future__ import annotations

import copy
from functools import lru_cache
import json
import logging
from pathlib import Path
import re

from .provisioning.models import ProvisioningError

MANIFEST = Path(__file__).with_name('contracts') / 'meta_business_pages_20261008.json'
log = logging.getLogger('remask_worker')


@lru_cache(maxsize=1)
def manifest():
    value = json.loads(MANIFEST.read_text())
    if value.get('version') != 1 or set(value.get('operations', {})) != {'CREATE_FP', 'READ_FP', 'FP_CATEGORY', 'FP_PRECHECK'}:
        raise ValueError('Invalid Business Page manifest')
    for op, row in value['operations'].items():
        if (row.get('endpoint_url') != 'https://business.facebook.com/api/graphql/'
                or not re.fullmatch(r'\d{5,40}', row.get('doc_id', ''))
                or row.get('operation_kind') != ('mutation' if op == 'CREATE_FP' else 'query')
                or not isinstance(row.get('variables'), dict)):
            raise ValueError('Invalid pinned Page operation: ' + op)
    return value


def command(operation, **bindings):
    def render(value):
        if isinstance(value, str) and value.startswith('$'):
            key = value[1:]
            if key not in bindings:
                raise ProvisioningError('PRIVATE_STATIC_CONTRACT_ARGUMENT_MISSING', 'Missing Page argument: ' + key)
            result = bindings[key]
            if key == 'business' and not re.fullmatch(r'\d{5,30}', str(result)):
                raise ProvisioningError('INVALID_INPUT', 'Page CREATE requires an exact numeric Business.')
            if key == 'categories' and (not isinstance(result, list) or not result
                    or any(not re.fullmatch(r'\d{5,30}', str(x)) for x in result)):
                raise ProvisioningError('PRIVATE_FAN_PAGE_CATEGORY_UNCONFIRMED', 'Exact Meta category IDs are required.')
            return copy.deepcopy(result)
        if isinstance(value, dict):
            return {key: render(child) for key, child in value.items()}
        if isinstance(value, list):
            return [render(child) for child in value]
        return value
    row = manifest()['operations'][operation]
    return {key: row[key] for key in ('doc_id', 'friendly_name', 'endpoint_url', 'operation_kind')} | {'variables': render(row['variables'])}


async def execute(web, operation, *, business, before_submit=None, **bindings):
    row = command(operation, business=business, **bindings)
    if operation == 'CREATE_FP' and not callable(before_submit):
        raise ProvisioningError('PRIVATE_STATIC_INTENT_REQUIRED', 'Durable Page intent must precede CREATE.')
    log.info('FP static_contract operation=%s doc_id=%s business=%s revision=%s browser_started=False',
        operation, row['doc_id'], business, manifest()['revision'])
    return await web.graphql(row['doc_id'], row['variables'], friendly_name=row['friendly_name'],
        endpoint_url=row['endpoint_url'], business_context_id=business,
        **({'before_submit': before_submit} if before_submit is not None else {}))
