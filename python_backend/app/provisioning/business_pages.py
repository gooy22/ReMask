"""Durable Page identity for each Facebook actor/Business, independent of slot order."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

from .advertising_page import AdvertisingPageStore, DEFAULT_PAGE_NAME
from .models import ProvisioningError, ProvisioningStep

_LOCKS: dict[str, asyncio.Lock] = {}
log = logging.getLogger('remask.prepare')


class BusinessPageStore:
    def __init__(self, state, context, business_id):
        self.state = state
        self.common = AdvertisingPageStore.for_context(state, context)
        self.actor_key = self.common.key
        self.business_id = str(business_id or '').strip()
        if not self.business_id.isdigit():
            raise ProvisioningError('INVALID_INPUT', 'A numeric Business is required for a bundle Page.')

    def _connect(self):
        db = sqlite3.connect(str(self.state.path), timeout=10)
        db.execute('CREATE TABLE IF NOT EXISTS workspace_business_pages ('
            'actor_key TEXT NOT NULL, business_id TEXT NOT NULL, page_id TEXT, value TEXT NOT NULL, '
            'PRIMARY KEY(actor_key,business_id), UNIQUE(actor_key,page_id))')
        return db

    def _read(self):
        with self._connect() as db:
            row = db.execute('SELECT value FROM workspace_business_pages WHERE actor_key=? AND business_id=?',
                (self.actor_key, self.business_id)).fetchone()
        return json.loads(row[0]) if row else {'name': DEFAULT_PAGE_NAME, 'business_id': self.business_id}

    async def get(self):
        value = await asyncio.to_thread(self._read)
        if value.get('page_id'):
            return value
        # Migrate only proven ownership. Never copy the first Page into all BMs.
        legacy = await self.common.get()
        grant = (legacy.get('grants') or {}).get(self.business_id) or {}
        claim = (grant.get('private_operations') or {}).get('claim_page') or {}
        owner = str(legacy.get('owner_business_id') or '')
        proven = (legacy.get('owner_business_confirmed') is True and owner == self.business_id)
        proven = proven or (not (legacy.get('owner_business_confirmed') is True and owner != self.business_id)
            and claim.get('status') == 'CONFIRMED'
            and str((claim.get('proof') or {}).get('owner_business_id') or '') == self.business_id)
        if str(legacy.get('page_id') or '').isdigit() and proven:
            return await self.patch(**{**legacy, 'business_id': self.business_id,
                'grants': {self.business_id: grant}})
        historical = await asyncio.to_thread(self._historical)
        if historical and not (legacy.get('page_id') == historical['page_id']
                and legacy.get('owner_business_confirmed') is True and owner != self.business_id):
            return await self.patch(page_id=historical['page_id'], name=historical.get('page_name') or DEFAULT_PAGE_NAME,
                owner_business_id=self.business_id, owner_business_confirmed=True,
                ownership_phase='PAGE_OWNERSHIP_CONFIRMED')
        return value

    def _historical(self):
        with sqlite3.connect(str(self.state.path), timeout=10) as db:
            rows = db.execute('SELECT result_json FROM provisioning_steps WHERE profile_id=? '
                'AND step=? AND status=? AND result_json IS NOT NULL ORDER BY updated_at DESC',
                (self.common.profile_id, ProvisioningStep.PAGE_ACCESS.value, 'SUCCESS')).fetchall()
        for row in rows:
            try:
                value = json.loads(row[0])
            except (ValueError, TypeError):
                continue
            if not isinstance(value, dict):
                continue
            if (str(value.get('business_id') or '') == self.business_id
                    and str(value.get('page_id') or '').isdigit()
                    and all(value.get(flag) is True for flag in ('page_owned_by_business',
                        'operator_full_control_verified', 'rk_operator_full_control_verified'))):
                return value
        return None

    def _patch(self, changes):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM workspace_business_pages WHERE actor_key=? AND business_id=?',
                (self.actor_key, self.business_id)).fetchone()
            old = json.loads(row[0]) if row else {'name': DEFAULT_PAGE_NAME, 'business_id': self.business_id}
            value = {**old, **changes, 'business_id': self.business_id}
            page = str(value.get('page_id') or '')
            if page and not page.isdigit():
                raise ProvisioningError('INVALID_INPUT', 'Bundle Page identity must be numeric.')
            if old.get('page_id') and page != old['page_id']:
                raise ProvisioningError('BUNDLE_PAGE_CHECKPOINT_MISMATCH', 'A retained bundle Page cannot be replaced during retry.')
            try:
                db.execute('INSERT INTO workspace_business_pages VALUES (?,?,?,?) '
                    'ON CONFLICT(actor_key,business_id) DO UPDATE SET page_id=excluded.page_id,value=excluded.value',
                    (self.actor_key, self.business_id, page or None, json.dumps(value)))
            except sqlite3.IntegrityError as exc:
                raise ProvisioningError('BUNDLE_PAGE_ALREADY_RESERVED',
                    'This Page is reserved for another Business of the same Facebook profile.') from exc
        return value

    async def patch(self, **changes):
        return await asyncio.to_thread(self._patch, changes)

    def _bindings(self):
        with self._connect() as db:
            rows = db.execute('SELECT business_id,page_id FROM workspace_business_pages '
                'WHERE actor_key=? AND page_id IS NOT NULL', (self.actor_key,)).fetchall()
        return {business: page for business, page in rows}

    async def bindings(self):
        return await asyncio.to_thread(self._bindings)


async def ensure_business_page(session, params, state, resolver=None):
    """Reuse an eligible exact Page or create one; CLAIM belongs to PAGE_ACCESS."""
    from ..static_meta_contracts import execute
    from ..private_page_ownership import _data, _id
    from .fan_pages_handler import fan_pages_handler

    store = BusinessPageStore(state, session.context, params.get('business_id'))
    lock = _LOCKS.setdefault(store.actor_key, asyncio.Lock())
    async with lock:
        config = await store.get()
        selected = str(params.get('page_id') or '').strip()
        if config.get('page_id'):
            if selected and selected != config['page_id']:
                raise ProvisioningError('BUNDLE_PAGE_CHECKPOINT_MISMATCH', 'Requested Page differs from the retained bundle Page.')
            log.info('BUNDLE_PAGE precheck profile=%s business=%s page=%s status=REUSED browser_started=False',
                session.context.profile_id, store.business_id, config['page_id'])
            return config

        desired_name = str(params.get('bundle_page_name') or '').strip()
        if desired_name and desired_name != config['name']:
            from ..private_business_fan_page_create import can_retarget_page_name
            creation_item = config.get('creation_item_id') or ('workspace-business-page-' + store.actor_key + '-' + store.business_id)
            saved = ((await state.step(creation_item, ProvisioningStep.FAN_PAGES)) or {}).get('result') or {}
            if not saved or can_retarget_page_name(saved):
                config = await store.patch(name=desired_name)
            # A sent, unresolved CREATE keeps its original name for verification.
            # Slot order and naming policy never replace a retained Page intent.

        legacy = await store.common.get()
        bindings = await store.bindings()
        excluded = {page for bm, page in bindings.items() if bm != store.business_id}
        if legacy.get('owner_business_confirmed') is True and legacy.get('owner_business_id') != store.business_id:
            if legacy.get('page_id'):
                excluded.add(str(legacy['page_id']))
        known = {str(row.get('id') or ''): row for row in [
            *await state.latest_profile_fan_pages(str(session.context.profile_id)),
            *(getattr(session.context, 'pages', None) or [])]
            if isinstance(row, dict) and str(row.get('id') or '').isdigit()}
        if legacy.get('page_id'):
            known[str(legacy['page_id'])] = {**legacy, 'id': str(legacy['page_id'])}
        candidates = [row for page, row in known.items() if not config.get('creation_item_id') and page not in excluded
            and (page == selected if selected else str(row.get('name') or '').casefold() == config['name'].casefold())]
        if selected and (selected in excluded or selected not in known):
            raise ProvisioningError('PROFILE_PAGE_UNAVAILABLE', 'Selected Page is absent or reserved for another bundle.')

        # Candidates are checked against the exact target BM; no name-only claim.
        for row in sorted(candidates, key=lambda value: int(value['id'])):
            web = await session.facebook_web()
            web.private_only = True
            bootstrap = await web.bootstrap()
            uid = str((getattr(session.context, 'cookies', {}) or {}).get('c_user') or '')
            if not uid.isdigit() or str(getattr(bootstrap, 'actor_id', '')) != uid:
                raise ProvisioningError('SESSION_EXPIRED', 'Page allocation HTTP actor does not match the selected profile.', retryable=True)
            response = await execute(web, 'PAGE', business=store.business_id, page=row['id'])
            node = _data(response).get('page')
            if not isinstance(node, dict) or _id(node.get('id')) != row['id'] or 'ownerBusiness' not in node:
                raise ProvisioningError('PRIVATE_PAGE_OWNERSHIP_INCONCLUSIVE', 'Existing Page ownership could not be verified before allocation.', retryable=True)
            owner = node['ownerBusiness']
            if owner is not None and (not isinstance(owner, dict) or not _id(owner.get('id'))):
                raise ProvisioningError('PRIVATE_PAGE_OWNERSHIP_INCONCLUSIVE', 'Existing Page owner is incomplete.', retryable=True)
            owner_id = _id(owner.get('id')) if isinstance(owner, dict) else ''
            if owner_id and owner_id != store.business_id:
                excluded.add(row['id'])
                if selected:
                    raise ProvisioningError('PAGE_OWNED_BY_ANOTHER_BUSINESS', 'Selected Page belongs to BM ' + owner_id + '.')
                continue
            if owner_id != store.business_id and ('permission_to_claim_to_business' not in node
                    or node.get('permission_to_claim_to_business') == 'REJECTED'):
                raise ProvisioningError('PRIVATE_PAGE_CLAIM_PERMISSION_UNCONFIRMED', 'Permission to add the existing Page is unconfirmed.', retryable=True)
            config = await store.patch(page_id=row['id'], name=row.get('name') or DEFAULT_PAGE_NAME,
                grants={store.business_id: (legacy.get('grants') or {}).get(store.business_id) or {}}
                    if legacy.get('page_id') == row['id'] else {})
            log.info('BUNDLE_PAGE commit profile=%s business=%s page=%s source=existing_page browser_started=False',
                session.context.profile_id, store.business_id, config['page_id'])
            return config

        if params.get('reuse_only') is True:
            raise ProvisioningError('PROFILE_PAGE_REQUIRED', 'This Business needs its own eligible existing Page.', retryable=True)
        # Stable across jobs, local aliases and reordered slots. The fan-page
        # state machine reconciles a retained submit before another CREATE.
        item = config.get('creation_item_id') or ('workspace-business-page-' + store.actor_key + '-' + store.business_id)
        creator = str(config.get('creation_profile_id') or session.context.profile_id)
        config = await store.patch(creation_item_id=item, creation_profile_id=creator)
        scope = 'workspace-business-page:' + store.business_id
        log.info('BUNDLE_PAGE execute profile=%s business=%s action=CREATE_PAGE item=%s reserved_pages=%s browser_started=False',
            session.context.profile_id, store.business_id, item, sorted(excluded))
        await state.set_running(item, creator, scope, ProvisioningStep.FAN_PAGES)
        result = await fan_pages_handler(session, {
            'names': [config['name']], 'count': 1, 'business_id': store.business_id,
            'business_suite_page_create': True,
            'defer_business_attach': True, 'reserved_page_ids': sorted(set(known) | excluded),
            'foreign_page_ids': sorted(excluded),
            'category': params.get('category') or 'Digital creator', 'confirm_main_business': False,
            'require_policy_consent': True, 'policies_accepted': params.get('policies_accepted') is not False,
        }, {}, provisioning_state=state, item_id=item, profile_id=creator, scope_key=scope)
        page = result['pages'][0]
        if str(page['id']) in excluded:
            raise ProvisioningError('BUNDLE_PAGE_ALREADY_RESERVED', 'Page CREATE resolved to another bundle; its ownership was preserved.')
        config = await store.patch(page_id=str(page['id']), name=page['name'])
        await state.complete(item, creator, scope, ProvisioningStep.FAN_PAGES, result)
        log.info('BUNDLE_PAGE commit profile=%s business=%s page=%s source=create_page browser_started=False',
            session.context.profile_id, store.business_id, config['page_id'])
        # Retain the legacy display Page only for the first bundle.
        if not legacy.get('page_id'):
            await store.common.patch(page_id=config['page_id'], name=config['name'])
        return config
