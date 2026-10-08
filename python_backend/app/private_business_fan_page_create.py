"""Business-owned Page PRECHECK -> EXECUTE -> VERIFY -> COMMIT over HTTP/2.

The canonical Page ID is delegate_page.id, never additional_profile.id. A
retained submit is reconciled inside its exact Business before any new CREATE.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from .business_fan_page_contracts import execute
from .private_page_ownership import _data, _id
from .static_meta_contracts import execute as settings_execute
from .provisioning.models import ProvisioningError, ProvisioningStep

log = logging.getLogger('remask_worker')
TRANSPORT = 'business_suite_page_static_http2'


async def read_pages(web, business):
    """Only canonical PAGE edges of this exact Business count as inventory."""
    rows, cursor, seen = {}, None, set()
    valid = True
    for _ in range(20):
        payload = await execute(web, 'READ_FP', business=business, cursor=cursor)
        node = _data(payload).get('node')
        if not isinstance(node, dict) or _id(node.get('id')) != business:
            raise ProvisioningError('PRIVATE_FAN_PAGE_INVENTORY_INCONCLUSIVE',
                'Page inventory response did not identify the exact Business.', retryable=True)
        connection = node.get('connected_objects')
        if not isinstance(connection, dict) or not isinstance(connection.get('edges'), list):
            raise ProvisioningError('PRIVATE_FAN_PAGE_INVENTORY_INCONCLUSIVE',
                'Exact-Business Page connection was not returned.', retryable=True)
        for edge in connection['edges']:
            asset = edge.get('node') if isinstance(edge, dict) else None
            if not isinstance(asset, dict):
                valid = False
                continue
            ids = {_id(asset.get(key)) for key in ('business_object_id', 'assetID') if asset.get(key) is not None}
            if not ids and asset.get('__typename') == 'Page':
                ids = {_id(asset.get('id'))}
            kind = asset.get('business_asset_type', asset.get('assetType'))
            if (len(ids) != 1 or '' in ids or (kind != 'PAGE' and not (kind is None and asset.get('__typename') == 'Page'))):
                valid = False
                continue
            page = next(iter(ids))
            # nameColumn/phoneColumn alias the SAME edge.node. Never search
            # incidental owner/IG/phone entities elsewhere in the response.
            name = asset.get('business_object_name', asset.get('name'))
            column = edge.get('nameColumn')
            strategy = column.get('bizkit_settings_render_strategy_no_business_id') if isinstance(column, dict) else None
            detail = strategy.get('business_object') if isinstance(strategy, dict) else None
            if isinstance(detail, dict) and _id(detail.get('business_object_id')) == page:
                name = detail.get('business_object_name', name)
            phone = edge.get('phoneColumn')
            if not name and isinstance(phone, dict) and _id(phone.get('business_object_id') or phone.get('id')) == page:
                name = phone.get('business_object_name')
            if not isinstance(name, str) or not name.strip():
                valid = False
                continue
            row = {'id': page, 'name': name.strip(), 'business_id': business}
            if page in rows and rows[page] != row:
                valid = False
            rows[page] = row
        info = connection.get('page_info')
        next_page = info.get('has_next_page') if isinstance(info, dict) else None
        if next_page is False:
            log.info('FP inventory business=%s pages=%s complete=%s browser_started=False', business, sorted(rows), valid)
            return list(rows.values()), valid
        next_cursor = info.get('end_cursor') if isinstance(info, dict) else None
        if next_page is not True or not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
            break
        seen.add(next_cursor)
        cursor = next_cursor
    return list(rows.values()), False


async def prove_page(web, business, page, name, excluded):
    if not _id(page) or page in excluded:
        return None
    payload = await settings_execute(web, 'PAGE', business=business, page=page)
    node = _data(payload).get('page')
    owner = node.get('ownerBusiness') if isinstance(node, dict) else None
    if (isinstance(node, dict) and _id(node.get('id')) == page and node.get('name') == name
            and isinstance(owner, dict) and _id(owner.get('id')) == business):
        return {'id': page, 'name': name, 'business_id': business}
    return None


async def reconcile(web, *, business, name, before, response_page=''):
    """Positive proof only. A delayed empty read never authorizes another POST."""
    reasons = []
    for attempt in range(3):
        try:
            if response_page:
                proof = await prove_page(web, business, response_page, name, before)
                if proof:
                    return proof
            else:
                rows, complete = await read_pages(web, business)
                matches = [row for row in rows if row['id'] not in before and row['name'] == name]
                if complete and len(matches) == 1:
                    proof = await prove_page(web, business, matches[0]['id'], name, before)
                    if proof:
                        return proof
            reasons.append('exact_page_unconfirmed')
        except Exception as exc:
            reasons.append(getattr(exc, 'code', type(exc).__name__))
            if type(exc).__name__ == 'AuthenticationError' or getattr(exc, 'code', '') in {'SESSION_EXPIRED', 'CHECKPOINT_REQUIRED', 'TWO_FACTOR_REQUIRED'}:
                raise ProvisioningError('SESSION_EXPIRED',
                    'Restore the profile session to verify the retained Page CREATE.', retryable=True) from exc
        if attempt < 2:
            await asyncio.sleep(0.5)
    log.info('FP reconciliation business=%s page=%s reasons=%s browser_started=False', business, response_page, reasons)
    raise ProvisioningError('FAN_PAGE_CREATE_RESULT_UNKNOWN',
        'Page CREATE is retained for BM ' + business + '; exact Page ownership has not yet been confirmed. No duplicate CREATE was sent.', retryable=True)


async def create_business_page(session, params, *, state, item_id, profile_id, scope_key, checkpoint):
    business = _id(params.get('business_id'))
    names = params.get('names')
    if not business or not isinstance(names, list) or len(names) != 1 or not isinstance(names[0], str) or not names[0].strip():
        raise ProvisioningError('INVALID_INPUT', 'Business Page creation requires one exact name and Business.')
    name, category = names[0].strip(), str(params.get('category') or 'Digital creator').strip()
    if len(name) > 120 or not category:
        raise ProvisioningError('INVALID_INPUT', 'Invalid Page name or category.')
    if params.get('policies_accepted') is not True:
        raise ProvisioningError('PAGE_POLICIES_CONFIRMATION_REQUIRED', 'Page policies must be accepted before CREATE.', retryable=True)
    actor = _id((getattr(session.context, 'cookies', {}) or {}).get('c_user'))
    web = await session.facebook_web()
    web.private_only = True
    bootstrap = await web.bootstrap()
    if not actor or str(getattr(bootstrap, 'actor_id', '')) != actor:
        raise ProvisioningError('SESSION_EXPIRED', 'Page HTTP actor differs from the selected profile.', retryable=True)
    if ((checkpoint.get('business_id') and checkpoint['business_id'] != business)
            or (checkpoint.get('target_names') and checkpoint['target_names'] != [name])
            or (checkpoint.get('create_actor_id') and checkpoint['create_actor_id'] != actor)):
        raise ProvisioningError('FAN_PAGES_CHECKPOINT_MISMATCH', 'Retained Page intent belongs to another target or actor.')

    async def save(patch):
        await state.checkpoint(item_id, profile_id, scope_key, ProvisioningStep.FAN_PAGES,
            {'business_id': business, 'target_names': [name], 'create_actor_id': actor, 'transport': TRANSPORT, **patch})

    reserved = {_id(value) for value in params.get('reserved_page_ids') or []}
    reserved.discard('')
    foreign = {_id(value) for value in params.get('foreign_page_ids') or []}
    foreign.discard('')
    phase = str(checkpoint.get('phase') or '').upper()
    pending = phase in {'PAGE_CREATE_CLICK_INTENT', 'PAGE_CREATE_RESULT_UNKNOWN'} or checkpoint.get('resume_from') == 'RECONCILE_CREATE'
    saved = [row for row in checkpoint.get('created_pages') or [] if isinstance(row, dict) and _id(row.get('id')) and row.get('name') == name]
    if saved:
        if len(saved) != 1:
            raise ProvisioningError('FAN_PAGES_CHECKPOINT_MISMATCH', 'Multiple Pages in a one-Page bundle checkpoint.')
        proof = await prove_page(web, business, saved[0]['id'], name, foreign)
        if not proof:
            raise ProvisioningError('PRIVATE_PAGE_OWNERSHIP_INCONCLUSIVE', 'Retained Page ownership could not be reverified.', retryable=True)
        reused = True
    elif pending:
        before = foreign | {_id(value) for value in checkpoint.get('active_before_ids') or []}
        proof = await reconcile(web, business=business, name=name, before=before,
            response_page=_id(checkpoint.get('response_page_id') or (checkpoint.get('browser_diagnostic') or {}).get('response_page_id')))
        reused = True
    else:
        previous = await state.latest_uncertain_fan_page(profile_id, name, exclude_item_id=item_id, business_id=business)
        if previous:
            old = previous['result']
            # An unscoped historical submit cannot be reinterpreted as this BM.
            if old.get('business_id') != business:
                raise ProvisioningError('FAN_PAGE_CREATE_RESULT_UNKNOWN', 'An older unscoped Page CREATE requires reconciliation before this bundle can create a Page.', retryable=True)
            patch = {key: old[key] for key in ('active_before_ids', 'response_page_id', 'browser_diagnostic') if key in old}
            await save({'phase': 'PAGE_CREATE_RESULT_UNKNOWN', 'resume_from': 'RECONCILE_CREATE', 'active_page_name': name, **patch})
            proof = await reconcile(web, business=business, name=name,
                before=foreign | {_id(value) for value in old.get('active_before_ids') or []},
                response_page=_id(old.get('response_page_id') or (old.get('browser_diagnostic') or {}).get('response_page_id')))
            reused = True
        else:
            rows, complete = await read_pages(web, business)
            if not complete:
                raise ProvisioningError('PRIVATE_FAN_PAGE_INVENTORY_INCONCLUSIVE', 'Exact-Business Page inventory is incomplete; no CREATE was sent.', retryable=True)
            # Resolver history can already contain an eligible Page in THIS BM.
            # Only foreign bundle reservations prohibit reuse. All known IDs
            # still join the immutable baseline before an actual new CREATE.
            matches = [row for row in rows if row['name'] == name and row['id'] not in foreign]
            if len(matches) > 1:
                raise ProvisioningError('PRIVATE_FAN_PAGE_TARGET_AMBIGUOUS', 'Multiple unreserved Pages have the requested name in this Business; no CREATE was sent.', retryable=True)
            if matches:
                proof = await prove_page(web, business, matches[0]['id'], name, foreign)
                if not proof:
                    raise ProvisioningError('PRIVATE_PAGE_OWNERSHIP_INCONCLUSIVE', 'Existing exact-Business Page ownership was not confirmed.', retryable=True)
                reused = True
            else:
                eligibility = _data(await execute(web, 'FP_PRECHECK', business=business)).get('business')
                gate = eligibility.get('showPageClaimBlockingDisclosures') if isinstance(eligibility, dict) else None
                if not isinstance(eligibility, dict) or _id(eligibility.get('id')) != business or not isinstance(gate, dict) or type(gate.get('passes_gk')) is not bool:
                    raise ProvisioningError('PRIVATE_FAN_PAGE_PRECHECK_INCONCLUSIVE', 'Page disclosure precheck did not identify the exact Business; no CREATE was sent.', retryable=True)
                if gate['passes_gk']:
                    raise ProvisioningError('PAGE_DISCLOSURE_REQUIRED', 'Meta requires its Business Page disclosure flow before creation; no CREATE was sent.', retryable=False)
                categories = _data(await execute(web, 'FP_CATEGORY', business=business, category=category))
                result = categories.get('page_creation_category_typeahead_search')
                result = result.get('results') if isinstance(result, dict) else None
                nodes = result.get('nodes') if isinstance(result, dict) else None
                matched = {_id(row.get('category_id')) for row in nodes or [] if isinstance(row, dict) and row.get('category_name') == category}
                if len(matched) != 1 or '' in matched:
                    raise ProvisioningError('PRIVATE_FAN_PAGE_CATEGORY_UNCONFIRMED', 'Meta category search did not return one exact numeric category; no CREATE was sent.', retryable=True)
                before = reserved | {row['id'] for row in rows}
                submitted = False
                async def intent():
                    nonlocal submitted
                    await save({'phase': 'PAGE_CREATE_CLICK_INTENT', 'resume_from': 'RECONCILE_CREATE',
                        'active_page_name': name, 'active_before_ids': sorted(before), 'category': category,
                        'category_ids': sorted(matched), 'response_page_id': ''})
                    submitted = True
                response_page, profile = '', ''
                try:
                    payload = await execute(web, 'CREATE_FP', business=business, name=name, bio=str(params.get('bio') or '')[:255],
                        categories=sorted(matched), join=str(uuid.uuid4()), before_submit=intent)
                    data = payload.get('data') if isinstance(payload, dict) else None
                    result = data.get('additional_profile_plus_create') if isinstance(data, dict) else None
                    additional = result.get('additional_profile') if isinstance(result, dict) else None
                    delegate = additional.get('delegate_page') if isinstance(additional, dict) else None
                    response_page = _id(delegate.get('id')) if isinstance(delegate, dict) else ''
                    profile = _id(additional.get('id')) if isinstance(additional, dict) else ''
                    error_code = str(result.get('error_code') or '') if isinstance(result, dict) else ''
                    error_code = error_code if error_code.isdigit() else ''
                    error_category = result.get('error_category') if isinstance(result, dict) else ''
                    error_category = error_category if error_category in {'user', 'system', 'integrity'} else ''
                    await save({'phase': 'PAGE_CREATE_RESULT_UNKNOWN', 'resume_from': 'RECONCILE_CREATE',
                        'active_page_name': name, 'response_page_id': response_page, 'additional_profile_id': profile,
                        'meta_error_code': error_code, 'meta_error_category': error_category,
                        'response_has_errors': bool(isinstance(payload, dict) and (payload.get('errors') or payload.get('error')))
                            or bool(isinstance(result, dict) and any(result.get(key) for key in ('name_error', 'error_message', 'error_code')))})
                    log.info('FP response business=%s delegate_page=%s additional_profile=%s error_code=%s error_category=%s browser_started=False',
                        business, response_page, profile, error_code, error_category)
                except Exception as exc:
                    if not submitted or getattr(exc, 'request_may_have_been_sent', None) is False:
                        await save({'phase': 'CREATE_NOT_SUBMITTED', 'resume_from': 'CREATE_NEXT', 'active_page_name': '', 'active_before_ids': []})
                        raise ProvisioningError('FAN_PAGE_CREATE_PRE_SUBMIT_TRANSPORT', 'Page HTTP precheck failed; CREATE was not sent.', retryable=True) from exc
                    # Intent is already durable. If the response checkpoint
                    # itself failed, retain the original intent for retry.
                    log.info('FP submit uncertain business=%s error_type=%s browser_started=False', business, type(exc).__name__)
                proof = await reconcile(web, business=business, name=name, before=before, response_page=response_page)
                reused = False

    page = {**proof, 'category': category, 'reused': reused, 'attached': True, 'already_attached': True}
    await save({'phase': 'PAGE_CREATED', 'resume_from': 'CREATE_NEXT', 'created_pages': [page],
        'active_page_name': '', 'active_before_ids': [], 'response_page_id': proof['id'], 'activity': 'FAN_PAGE_OWNERSHIP_VERIFIED'})
    log.info('FP commit business=%s page=%s source=%s browser_started=False', business, proof['id'], TRANSPORT)
    return {'phase': 'DONE', 'mode': 'create', 'requested_count': 1, 'created_count': 1, 'attached_count': 1,
        'page_ids': [page['id']], 'pages': [page], 'target_names': [name], 'category': category,
        'business_id': business, 'ad_account_id': '', 'page_business_attached': True,
        'ad_account_page_access_verified': False, 'attachment_scope': 'business', 'transport': TRANSPORT}
