"""One explicit, durable objective-only modify of an existing PAUSED draft.

The original Launch journal is immutable. An uncertain modify is never replayed;
accepted receipts resume only exact fragment reads, using a separate repair key.
No creation, child-object modification, payment, media or publication is routed.
"""
from __future__ import annotations

import copy
import hashlib
import json

from .private_ads_draft import FRAGMENT_FIELDS, LEVELS, fail, fragment_params, fragment_values_match, identity
from .private_ads_http import AdsManagerPrivateHttp
from .private_ads_inspect import adapter_scope, saved_draft_intent, validation_diagnostic
from .private_ads_publication import AdsManagerDraftPublisher
from .private_ads_state import AdsManagerSubmitter

REVIEWED_SOURCE = '9b2b414e40aa4d6a1d69dfdfefee07341a45fa18b4328a72f185e63c28d00c44'
OPERATION = 'REPAIR_OBJECTIVE'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _row(row, *, target, draft_id, level, fragment_id, values, objects, confirmed=None):
    adapter_scope(row, target['ad_account_id'])
    parent = None if level == 'campaign' else objects['campaign' if level == 'ad_set' else 'ad_set']
    if (row.get('id') != fragment_id or row.get('ad_draft_id') != draft_id
            or row.get('ad_object_type') != level or row.get('parent_ad_object_id') != parent
            or row.get('status') != 'EDITING' or row.get('publish_status') is not None):
        raise fail('REPAIR_SCOPE_MISMATCH', 'Objective repair requires the exact unpublished editing draft hierarchy')
    entity = identity(row.get('ad_object_id'))
    if confirmed is not None and entity != confirmed or entity in objects.values():
        raise fail('REPAIR_SCOPE_MISMATCH', 'Objective repair fragment object identity changed or is duplicated')
    if values.get('status') != 'PAUSED' or not fragment_values_match(row.get('values'), values, business_id=target['business_id']):
        raise fail('REPAIR_SPEC_MISMATCH', 'Objective repair cannot change a draft with different saved settings')
    if level != 'campaign' and values.get('campaign_id') != objects['campaign']:
        raise fail('REPAIR_SCOPE_MISMATCH', 'Objective repair child belongs to another Campaign')
    if level == 'ad' and values.get('adset_id') != objects['ad_set']:
        raise fail('REPAIR_SCOPE_MISMATCH', 'Objective repair Ad belongs to another Ad Set')
    return entity


def _report(level, fragment_id, row):
    return {'level': level, 'fragment_id': fragment_id, 'ad_object_id': row['ad_object_id'],
        'settings_match': True, 'status': row.get('status'), 'publish_status': row.get('publish_status'),
        'validation_status': row.get('validation_status'),
        'active_error_diagnostic': validation_diagnostic(row.get('active_errors'), observed='active_errors' in row)}


async def repair_saved_objective(adapter, *, profile_id, context, session, payload):
    if payload.get('repair_objective') is not True or 'reconcile_only' in payload:
        raise fail('INVALID_INPUT', 'Objective repair must be explicitly selected independently of inspection')
    target, key, fingerprint, actor, source, draft_id, fragments, pending = await saved_draft_intent(
        adapter, profile_id=profile_id, context=context, payload=payload)
    # Diagnostic inspection may inspect a partial prefix; a write-capable
    # objective correction must retain the complete immutable hierarchy.
    if pending is not None or draft_id is None or len(fragments) != len(LEVELS):
        raise fail('RECONCILE_REQUIRED', 'Objective repair requires all saved draft receipts and verified relationships')
    # This is the reviewed controlled attempt, not a generic journal upgrader.
    if source != REVIEWED_SOURCE:
        raise fail('REPAIR_SOURCE_UNREVIEWED', 'Only the reviewed retained sender is eligible for objective repair')
    _, launch, _, _ = adapter.prepare(profile_id, payload)
    if (launch.get('execution_mode') != 'DRAFT'
            or any(value != 'PAUSED' for value in launch.get('delivery', {}).values())):
        raise fail('INITIAL_STATUS', 'Objective repair requires an unpublished PAUSED draft')
    before = {level: values for level, _, values, _ in fragments}
    campaign = before['campaign']
    if campaign.get('objective') != 'OUTCOME_TRAFFIC' or 'is_odax_campaign_group' in campaign:
        raise fail('REPAIR_SPEC_MISMATCH', 'Saved Campaign does not match the reviewed Traffic correction')
    after = adapter.objective_wire.campaign(campaign)
    changes = {name: after[name] for name in ('objective', 'is_odax_campaign_group')}
    if {name for name in set(campaign) | set(after) if campaign.get(name) != after.get(name)} != set(changes):
        raise fail('REPAIR_SPEC_MISMATCH', 'Objective repair must change only the two reviewed Campaign fields')
    binding = {'kind':'retained-objective-repair-v1', 'target':target, 'launch_key':key,
        'launch_fingerprint':fingerprint, 'original_source':source, 'actor':actor,
        'draft_id':draft_id, 'fragments':fragments, 'changes':changes}
    repair_key = 'private-objective-repair:' + _digest(binding)
    repair_fingerprint = _digest({**binding, 'contract':adapter.objective_wire.digest})
    state = adapter.owner.state
    prior_rows = await state.all_steps(repair_key)
    for prior in prior_rows:
        evidence = prior.get('result', {}).get('evidence', {})
        if (prior.get('step') != 'ADS_MANAGER:' + OPERATION
                or any(str(prior.get(field)) != target[field] for field in ('profile_id','business_id','ad_account_id'))
                or evidence.get('actor_id') != actor or evidence.get('launch_fingerprint') != repair_fingerprint
                or evidence.get('source_digest') != adapter.source_digest):
            raise fail('SAVED_OPERATION_MISMATCH', 'Saved objective repair has another target, contract or configuration')
        if not isinstance(evidence.get('receipt'), dict) or not evidence['receipt'].get('fragment_id'):
            raise fail('RECONCILE_REQUIRED', 'Objective repair has an uncertain saved reply; automatic replay is blocked')
    await state.bind_plan(repair_key, target, repair_fingerprint, _digest({'source':adapter.source_digest,'actor':actor}))
    saved_receipt = prior_rows[0]['result']['evidence']['receipt'] if prior_rows else None
    if saved_receipt:
        identity(saved_receipt['fragment_id'])
    web = await session.facebook_web()
    client = await AdsManagerPrivateHttp.open(web, account_id=target['ad_account_id'], business_id=target['business_id'], actor_id=actor)
    if (client.context.account_id != target['ad_account_id'] or client.context.business_id != target['business_id']
            or client.context.actor_id != actor):
        raise fail('REPAIR_SCOPE_MISMATCH', 'Objective repair HTTP context belongs to another profile or account')
    # All pre-write operations are reads, even though this explicit operation
    # may later dispatch its single modify through the durable submitter.
    client.read_only = True
    account = await client.get('/act_' + target['ad_account_id'], {'fields':['id','account_id','business']})
    if (not isinstance(account, dict) or account.get('id') not in {target['ad_account_id'], 'act_' + target['ad_account_id']}
            or not isinstance(account.get('business'), dict) or account['business'].get('id') != target['business_id']):
        raise fail('REPAIR_SCOPE_MISMATCH', 'Fresh account read did not confirm the saved Business')
    draft = await client.get('/addraft_' + draft_id, {'fields':['id','account_id','author_id','ownership_type']})
    adapter_scope(draft, target['ad_account_id'])
    if draft.get('id') != draft_id or draft.get('author_id') != actor or draft.get('ownership_type') != 'USER':
        raise fail('REPAIR_SCOPE_MISMATCH', 'Fresh draft ownership differs from the saved intent')
    objects, reads = {}, []
    for level, fragment_id, values, confirmed in fragments:
        if level == 'campaign' and saved_receipt:
            fragment_id, values = saved_receipt['fragment_id'], after
        row = await client.get('/addraftfragment_' + fragment_id, {'fields':FRAGMENT_FIELDS})
        objects[level] = _row(row, target=target, draft_id=draft_id, level=level, fragment_id=fragment_id,
            values=values, objects=objects, confirmed=confirmed)
        reads.append(_report(level, fragment_id, row))
    sender = AdsManagerSubmitter(state, launch_key=repair_key, profile_id=target['profile_id'],
        business_id=target['business_id'], account_id=target['ad_account_id'], actor_id=actor,
        launch_fingerprint=repair_fingerprint, source_digest=adapter.source_digest)
    params = fragment_params(draft_id, level='campaign', values=changes, object_id=objects['campaign'], action='modify')
    for entry in params['values']:
        entry['old_value'] = copy.deepcopy(campaign.get(entry['field']))
    # A saved receipt uses exactly the same request fingerprint without a POST.
    client.read_only = bool(saved_receipt)
    receipt = await sender.submit(client, operation=OPERATION, path='/' + draft_id + '/addraft_fragments',
        params=params, receipt=lambda row: {'fragment_id':identity(row.get('id'))}, saved_only=bool(saved_receipt))
    client.read_only = True
    final, confirmed_objects = [], {}
    for level, fragment_id, values, confirmed in fragments:
        if level == 'campaign':
            fragment_id, values = receipt['fragment_id'], after
        row = await client.get('/addraftfragment_' + fragment_id, {'fields':FRAGMENT_FIELDS})
        confirmed_objects[level] = _row(row, target=target, draft_id=draft_id, level=level,
            fragment_id=fragment_id, values=values, objects=confirmed_objects, confirmed=objects[level])
        final.append(_report(level, fragment_id, row))
    campaign_read = final[0]
    if campaign_read['validation_status'] == 'HAS_ERRORS' or campaign_read['active_error_diagnostic']['has_errors']:
        raise fail('VALIDATION_ERRORS', 'Meta rejected the retained Campaign objective correction; the saved modify cannot replay')
    if campaign_read['validation_status'] != 'VALIDATED':
        raise fail('VALIDATION_PENDING', 'Meta has not confirmed Campaign correction validation; resume its saved read', True)
    await AdsManagerDraftPublisher(sender)._confirm(OPERATION, {'draft_id':draft_id,
        'fragment_id':receipt['fragment_id'], 'ad_object_id':objects['campaign'],
        'fragment_saved':True,'meta_validation_status':'VALIDATED','editor_verified':False})
    return {'status':'SUCCESS', 'execution_mode':'DRAFT_OBJECTIVE_REPAIR', **target,
        'launch_key':key, 'repair_key':repair_key, 'draft_id':draft_id, 'draft_objects':confirmed_objects,
        'fragment_reads':final, 'campaign_correction_verified':True, 'original_journal_unchanged':True,
        'mutation_posts':0 if saved_receipt else 1, 'creation_posts':0, 'replayed_posts':0,
        'editor_verified':False,'editor_state':'EDITOR_UNVERIFIED','publication_ready':False,
        'published':False,'payment_checked_live':False, 'all_fragments_validated': all(
            row['validation_status']=='VALIDATED' and not row['active_error_diagnostic']['has_errors'] for row in final)}
