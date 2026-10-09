"""Business-owned Page PRECHECK -> EXECUTE -> VERIFY -> COMMIT over HTTP/2.

The canonical Page ID is delegate_page.id, never additional_profile.id. A
retained submit is reconciled inside its exact Business before any new CREATE.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from .business_fan_page_contracts import execute
from .business_page_response import inspect_create_response, rejection_error
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


async def prove_page(web, business, page, name, excluded, *, allow_unowned=False):
    if not _id(page) or page in excluded:
        return None
    payload = await settings_execute(web, 'PAGE', business=business, page=page)
    node = _data(payload).get('page')
    owner = node.get('ownerBusiness') if isinstance(node, dict) else None
    if (isinstance(node, dict) and _id(node.get('id')) == page and node.get('name') == name
            and isinstance(owner, dict) and _id(owner.get('id')) == business):
        return {'id': page, 'name': name, 'business_id': business}
    if (allow_unowned and isinstance(node, dict) and _id(node.get('id')) == page
            and node.get('name') == name and 'ownerBusiness' in node and owner is None
            and node.get('permission_to_claim_to_business') == 'ALLOWED'):
        # Creation and ownership are separate facts. PAGE_ACCESS must perform
        # Add an existing Page and verify the owner before granting rights.
        return {'id': page, 'name': name, 'business_id': '', 'requires_business_claim': True}
    return None


async def reconcile(web, *, business, name, before, response_page='', actor=''):
    """Positive proof only. A delayed empty read never authorizes another POST."""
    reasons = []
    ambiguous = False
    for attempt in range(3):
        try:
            if response_page:
                proof = await prove_page(web, business, response_page, name, before, allow_unowned=True)
                if proof:
                    return proof
            else:
                rows, complete = await read_pages(web, business)
                matches = [row for row in rows if row['id'] not in before and row['name'] == name]
                ambiguous = ambiguous or len(matches) > 1
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
    if actor and not response_page and not ambiguous:
        from .private_page_recovery import managed_page_candidates
        try:
            candidates = await managed_page_candidates(web, actor=actor, name=name, excluded=before)
            proofs = []
            for page in candidates[:10]:
                proof = await prove_page(web, business, page, name, before, allow_unowned=True)
                if proof:
                    proofs.append(proof)
            if len(candidates) <= 10 and len(proofs) == 1:
                log.info('FP reconciliation business=%s page=%s source=managed_profile_http requires_claim=%s browser_started=False',
                    business, proofs[0]['id'], proofs[0].get('requires_business_claim', False))
                return {**proofs[0], 'recovery_source': 'managed_profile_http'}
            reasons.append('managed_profile_page_not_unique' if len(proofs) > 1 else 'managed_profile_page_unconfirmed')
        except Exception as exc:
            reasons.append(getattr(exc, 'code', type(exc).__name__))
            if getattr(exc, 'code', '') in {'SESSION_EXPIRED', 'CHECKPOINT_REQUIRED', 'BUSINESS_LOGIN_GATE'}:
                raise
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
        return await state.checkpoint(item_id, profile_id, scope_key, ProvisioningStep.FAN_PAGES,
            {'business_id': business, 'target_names': [name], 'create_actor_id': actor, 'transport': TRANSPORT, **patch})

    reserved = {_id(value) for value in params.get('reserved_page_ids') or []}
    reserved.discard('')
    foreign = {_id(value) for value in params.get('foreign_page_ids') or []}
    foreign.discard('')
    phase = str(checkpoint.get('phase') or '').upper()
    response_evidence = checkpoint.get('create_response') or {}
    if phase == 'PAGE_CREATE_REJECTED' and response_evidence.get('outcome') == 'REJECTED':
        raise rejection_error(response_evidence)
    pending = phase in {'PAGE_CREATE_CLICK_INTENT', 'PAGE_CREATE_RESULT_UNKNOWN'} or checkpoint.get('resume_from') == 'RECONCILE_CREATE'
    saved = [row for row in checkpoint.get('created_pages') or [] if isinstance(row, dict) and _id(row.get('id')) and row.get('name') == name]
    if saved:
        if len(saved) != 1:
            raise ProvisioningError('FAN_PAGES_CHECKPOINT_MISMATCH', 'Multiple Pages in a one-Page bundle checkpoint.')
        proof = await prove_page(web, business, saved[0]['id'], name, foreign,
            allow_unowned=saved[0].get('requires_business_claim') is True)
        if not proof:
            raise ProvisioningError('PRIVATE_PAGE_OWNERSHIP_INCONCLUSIVE', 'Retained Page ownership could not be reverified.', retryable=True)
        reused = True
    elif pending:
        before = foreign | {_id(value) for value in checkpoint.get('active_before_ids') or []}
        proof = await reconcile(web, business=business, name=name, before=before,
            response_page=_id(checkpoint.get('response_page_id') or (checkpoint.get('browser_diagnostic') or {}).get('response_page_id')),
            actor=actor)
        reused = True
    else:
        previous = await state.latest_uncertain_fan_page(profile_id, name, exclude_item_id=item_id, business_id=business)
        if previous:
            old = previous['result']
            # An unscoped historical submit cannot be reinterpreted as this BM.
            if old.get('business_id') != business:
                raise ProvisioningError('FAN_PAGE_CREATE_RESULT_UNKNOWN', 'An older unscoped Page CREATE requires reconciliation before this bundle can create a Page.', retryable=True)
            patch = {key: old[key] for key in ('active_before_ids', 'response_page_id', 'browser_diagnostic', 'create_response') if key in old}
            await save({'phase': 'PAGE_CREATE_RESULT_UNKNOWN', 'resume_from': 'RECONCILE_CREATE', 'active_page_name': name, **patch})
            proof = await reconcile(web, business=business, name=name,
                before=foreign | {_id(value) for value in old.get('active_before_ids') or []},
                response_page=_id(old.get('response_page_id') or (old.get('browser_diagnostic') or {}).get('response_page_id')),
                actor=actor)
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
                attempt_id = str(uuid.uuid4())
                async def intent():
                    nonlocal submitted
                    await save({'phase': 'PAGE_CREATE_CLICK_INTENT', 'resume_from': 'RECONCILE_CREATE',
                        'active_page_name': name, 'active_before_ids': sorted(before), 'category': category,
                        'category_ids': sorted(matched), 'response_page_id': '',
                        'create_attempt_id': attempt_id, 'create_response': {}})
                    submitted = True
                response_page, profile = '', ''
                try:
                    payload = await execute(web, 'CREATE_FP', business=business, name=name, bio=str(params.get('bio') or '')[:255],
                        categories=sorted(matched), join=attempt_id, before_submit=intent)
                except Exception as exc:
                    from .private_auth import private_auth_error
                    auth_error = private_auth_error(exc)
                    if (not submitted or getattr(exc, 'request_may_have_been_sent', None) is False
                            or (auth_error is not None and getattr(exc, 'request_rejected', False) is True)):
                        await save({'phase': 'CREATE_NOT_SUBMITTED', 'resume_from': 'CREATE_NEXT', 'active_page_name': '', 'active_before_ids': []})
                        if auth_error is not None:
                            raise auth_error from exc
                        raise ProvisioningError('FAN_PAGE_CREATE_PRE_SUBMIT_TRANSPORT', 'Page HTTP precheck failed; CREATE was not sent.', retryable=True) from exc
                    # Intent is already durable. If the response checkpoint
                    # itself failed, retain the original intent for retry.
                    log.info('FP submit uncertain business=%s error_type=%s browser_started=False', business, type(exc).__name__)
                    if auth_error is not None:
                        raise auth_error from exc
                else:
                    evidence = inspect_create_response(payload)
                    response_evidence = evidence
                    response_page, profile = evidence['page_id'], evidence['additional_profile_id']
                    rejected = evidence['outcome'] == 'REJECTED'
                    await save({'phase': 'PAGE_CREATE_REJECTED' if rejected else 'PAGE_CREATE_RESULT_UNKNOWN',
                        'resume_from': 'MANUAL_REQUIRED' if rejected else 'RECONCILE_CREATE',
                        'active_page_name': name, 'response_page_id': response_page, 'additional_profile_id': profile,
                        'meta_error_code': evidence['meta_error_code'], 'meta_error_category': evidence['meta_error_category'],
                        'response_has_errors': evidence['response_has_errors'], 'create_response': evidence})
                    log.info('FP response business=%s attempt=%s evidence=%s browser_started=False',
                        business, attempt_id, json.dumps(evidence, separators=(',', ':')))
                    if rejected:
                        raise rejection_error(evidence)
                proof = await reconcile(web, business=business, name=name, before=before, response_page=response_page, actor=actor)
                reused = False

    attached = proof.get('business_id') == business
    page = {**proof, 'category': category, 'reused': reused, 'attached': attached, 'already_attached': attached}
    committed = await save({'phase': 'PAGE_CREATED', 'resume_from': 'CREATE_NEXT', 'created_pages': [page],
        'active_page_name': '', 'active_before_ids': [], 'response_page_id': proof['id'],
        'resolution_source': proof.get('recovery_source') or 'exact_business_page',
        'original_submit_response_recovered': False if proof.get('recovery_source') else bool(response_evidence.get('page_id')),
        'activity': 'FAN_PAGE_OWNERSHIP_VERIFIED' if attached else 'FAN_PAGE_IDENTITY_VERIFIED_CLAIM_REQUIRED'})
    log.info('FP commit business=%s page=%s source=%s browser_started=False', business, proof['id'], TRANSPORT)
    return {'phase': 'DONE', 'mode': 'create', 'requested_count': 1, 'created_count': 1, 'attached_count': int(attached),
        'page_ids': [page['id']], 'pages': [page], 'created_pages': [page], 'target_names': [name], 'category': category,
        'create_response': committed.get('create_response') or {}, 'create_attempt_id': committed.get('create_attempt_id') or '',
        'resolution_source': committed['resolution_source'],
        'original_submit_response_recovered': committed['original_submit_response_recovered'],
        'business_id': business, 'ad_account_id': '', 'page_business_attached': attached,
        'ad_account_page_access_verified': False, 'attachment_scope': 'business', 'transport': TRANSPORT}
