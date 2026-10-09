"""Explicit, one-shot retirement of an operator-authorized legacy Page intent.

No Meta request or Job is sent here. Original rows are archived atomically;
confirmed assets and new, journalled submit attempts are never reset.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import time

MANIFEST = Path(__file__).parents[1] / 'state_repairs' / 'page_intent_reset_20261009.json'
PHASE = 'PAGE_CREATE_RESET_AUTHORIZED'


def _has_identity(value):
    if any(value.get(key) is True for key in ('page_business_attached', 'page_owned_by_business', 'ad_account_page_access_verified')):
        return True
    if any(value.get(key) for key in ('response_page_id', 'additional_profile_id', 'page_id', 'page_ids')):
        return True
    if any(value.get(key) for key in ('pages', 'created_pages')):
        return True
    response = value.get('create_response') or {}
    diagnostic = value.get('browser_diagnostic') or {}
    return (not isinstance(response, dict) or not isinstance(diagnostic, dict)
        or any(response.get(key) for key in ('page_id', 'additional_profile_id'))
        or bool(diagnostic.get('response_page_id')))


def _eligible(row, value, repair):
    # Startup normalizes interrupted nested steps to QUEUED and changes their
    # updated_at. That timestamp measures reconciliation, not CREATE dispatch.
    # Legacy intent identity is instead guarded by the absent submit journal.
    return (row['profile_id'] == repair['profile_id'] and row['status'] in {'FAILED', 'QUEUED'}
        and value.get('business_id') == repair['business_id']
        and value.get('create_actor_id') == repair['actor_id']
        and value.get('active_page_name') == repair['page_name']
        and value.get('target_names') == [repair['page_name']]
        and value.get('phase') == 'PAGE_CREATE_RESULT_UNKNOWN'
        and value.get('transport') == 'business_suite_page_static_http2'
        and not value.get('create_attempt_id') and not _has_identity(value))


def _apply(state, repair):
    with state._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('CREATE TABLE IF NOT EXISTS provisioning_page_intent_resets ('
            'repair_id TEXT PRIMARY KEY, applied_at INTEGER NOT NULL, '
            'request_json TEXT NOT NULL, archived_steps_json TEXT NOT NULL)')
        if con.execute('SELECT 1 FROM provisioning_page_intent_resets WHERE repair_id=?',
                (repair['id'],)).fetchone():
            return {'repair_id': repair['id'], 'outcome': 'ALREADY_APPLIED'}
        rows = con.execute('SELECT * FROM provisioning_steps WHERE profile_id=? AND step=?',
            (repair['profile_id'], 'FAN_PAGES')).fetchall()
        values = {}
        for row in rows:
            try:
                value = json.loads(row['result_json'] or '{}')
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                values[row['item_id']] = value
        anchor = next((row for row in rows if row['item_id'] == repair['item_id']), None)
        if anchor is None:
            return {'repair_id': repair['id'], 'outcome': 'NOT_FOUND'}
        if not _eligible(anchor, values.get(anchor['item_id'], {}), repair):
            return {'repair_id': repair['id'], 'outcome': 'TARGET_CHANGED'}
        # A bound Page is authoritative even if a stale intent row is unknown.
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_business_pages'").fetchone():
            bound = con.execute('SELECT page_id FROM workspace_business_pages WHERE actor_key=? AND business_id=?',
                ('facebook:' + repair['actor_id'], repair['business_id'])).fetchone()
            if bound and bound['page_id']:
                return {'repair_id': repair['id'], 'outcome': 'PAGE_ALREADY_BOUND'}
        selected = [row for row in rows if _eligible(row, values.get(row['item_id'], {}), repair)]
        now = int(time.time())
        archived = [dict(row) for row in selected]
        con.execute('INSERT INTO provisioning_page_intent_resets VALUES (?,?,?,?)',
            (repair['id'], now, json.dumps(repair), json.dumps(archived)))
        for row in selected:
            value = {'business_id': repair['business_id'], 'create_actor_id': repair['actor_id'],
                'target_names': [repair['page_name']], 'transport': 'business_suite_page_static_http2',
                'phase': PHASE, 'resume_from': 'CREATE_NEXT', 'active_page_name': '',
                'active_before_ids': [], 'tombstone_page_name': repair['page_name'],
                'reset_authorization': {'repair_id': repair['id'], 'reason': repair['reason'],
                    'applied_at': now, 'original_result': 'UNKNOWN'}}
            con.execute('UPDATE provisioning_steps SET result_json=?,error_code=?,error_message=?,updated_at=? '
                'WHERE item_id=? AND step=?', (json.dumps(value), PHASE,
                    'Operator authorized retiring this unknown CREATE; the original evidence was archived.',
                    now, row['item_id'], 'FAN_PAGES'))
        return {'repair_id': repair['id'], 'outcome': 'APPLIED', 'reset_count': len(selected),
            'profile_id': repair['profile_id'], 'business_id': repair['business_id']}


async def apply_page_intent_resets(state, manifest=MANIFEST):
    path = Path(manifest)
    if not path.exists():
        return []
    document = json.loads(path.read_text())
    if document.get('version') != 1 or not isinstance(document.get('repairs'), list):
        raise ValueError('Invalid Page intent repair manifest')
    outcomes = []
    for repair in document['repairs']:
        if (not isinstance(repair, dict) or not all(isinstance(repair.get(key), str) and repair[key]
                for key in ('id', 'item_id', 'profile_id', 'business_id', 'actor_id', 'page_name', 'reason'))
                or not all(re.fullmatch(r'\d{5,30}', repair[key]) for key in ('business_id', 'actor_id'))
                or type(repair.get('authorized_at')) is not int):
            raise ValueError('Invalid scoped Page intent repair')
        outcomes.append(await asyncio.to_thread(_apply, state, repair))
    return outcomes
