"""Add an existing Page and assign full asset rights over the profile HTTP session."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

from .static_meta_contracts import StaticAssetContracts, contract_metadata
from .private_inventory import _auth_gate
from .provisioning.models import ProvisioningError

CONFIG, PAGE, RIGHTS, CLAIM, ASSIGN = (contract_metadata(op)['friendly_name'] for op in ('CONFIG', 'PAGE', 'RIGHTS', 'CLAIM', 'ASSIGN'))

log = logging.getLogger("remask_worker")
_PENDING = {"SUBMIT_INTENT", "RESULT_UNKNOWN", "RESULT_UNVERIFIED"}
_TASK_KEYS = {"assigned_task_ids", "assignedTaskIDs", "permitted_task_ids", "permittedTaskIDs",
    "assigned_tasks", "assignedTasks", "assigned_permission_task_ids", "assignedPermissionTaskIDs",
    "assigned_permission_tasks", "permitted_tasks", "task_ids"}


def _id(value):
    text = str(value or "").removeprefix("act_")
    return text if re.fullmatch(r"\d{5,30}", text) else ""


def _data(payload):
    if not isinstance(payload, dict) or payload.get("errors") or payload.get("error") or not isinstance(payload.get("data"), dict):
        raise ProvisioningError("PRIVATE_ASSET_READ_INCONCLUSIVE", "Meta did not return an error-free asset read response.", retryable=True)
    return payload["data"]


def _tasks(value):
    if not isinstance(value, list):
        return set()
    result = set()
    for row in value:
        if isinstance(row, dict):
            row = row.get("task_id", row.get("taskID", row.get("id")))
        if not _id(row):
            return set()
        result.add(_id(row))
    return result


def _standalone_assignment(data, asset_id, user_id, business_id=None):
    """Bind the sibling fields in the observed Settings permissions record.

    Do not collect an asset, user and tasks from unrelated branches. Meta's
    standalone query puts all three under one user_assigned_permissions record.
    """
    root = data.get('business_object_rendered_in_ui')
    record = root.get('user_assigned_permissions') if isinstance(root, dict) else None
    if not isinstance(record, dict):
        return None, 'missing_permissions_record'
    asset, user, business = (record.get(key) for key in ('asset', 'user', 'current_business'))
    if not isinstance(asset, dict) or asset_id not in {
            _id(asset.get(key)) for key in ('id', 'business_object_id', 'business_object_ui_id')}:
        return None, 'asset_mismatch'
    if not isinstance(user, dict) or _id(user.get('id')) != user_id:
        return None, 'business_user_mismatch'
    if business_id is not None and (not isinstance(business, dict) or _id(business.get('id')) != business_id):
        return None, 'business_mismatch'
    raw_tasks = record.get('assigned_permission_task_ids')
    tasks = _tasks(raw_tasks)
    if not isinstance(raw_tasks, list) or any(not _id(value) for value in raw_tasks):
        return None, 'assigned_tasks_malformed'
    return {'asset_id': asset_id, 'business_user_id': user_id,
            'assigned_task_ids': sorted(tasks)}, ''


def assignment_proof(payload, *, asset_id, user_id, required_tasks, business_id=None):
    """Require numeric assigned tasks under this exact asset/person relation.

    Configured/available tasks and business-wide permissions never prove an
    asset assignment. A read error or a conflicting relation fails closed.
    """
    data = _data(payload)
    if 'business_object_rendered_in_ui' in data:
        proof, _ = _standalone_assignment(data, asset_id, user_id, business_id)
        return proof if proof and set(required_tasks).issubset(proof['assigned_task_ids']) else None
    proofs = []
    def walk(value, in_asset=False, in_user=False):
        if isinstance(value, list):
            for child in value:
                walk(child, in_asset, in_user)
            return
        if not isinstance(value, dict):
            return
        typename = str(value.get("__typename") or "").lower()
        identities = {_id(value.get(k)) for k in ("id", "assetID", "business_object_id", "business_object_ui_id")}
        identities.discard("")
        if typename in {"page", "adaccount", "businessconnectedobject", "businessasset"}:
            if asset_id not in identities:
                return
            in_asset = True
        if typename in {"businessuser", "businessscopeduser", "businessscopeduserorrequest", "adbusinessuser"}:
            if _id(value.get("id")) != user_id:
                return
            in_user = True
        edge_user = value.get('node')
        if (in_asset and isinstance(edge_user, dict)
                and str(edge_user.get('__typename', '')).lower() in {'businessuser', 'businessscopeduser', 'adbusinessuser'}
                and _id(edge_user.get('id')) == user_id):
            in_user = True
        if in_asset and in_user:
            for key in _TASK_KEYS.intersection(value):
                tasks = _tasks(value[key])
                if set(required_tasks).issubset(tasks):
                    proofs.append({"asset_id": asset_id, "business_user_id": user_id, "assigned_task_ids": sorted(tasks)})
        for key, child in value.items():
            if key not in {"permissionTasksConfig", "permission_tasks_config", "available_permission_tasks", "owner_business", "owning_business"}:
                walk(child, in_asset, in_user)
    walk(data)
    return proofs[0] if proofs and all(row == proofs[0] for row in proofs) else None


def _assert_assignment_targets(payload, asset, user, business_id=None):
    data = _data(payload)
    if 'business_object_rendered_in_ui' in data:
        proof, reason = _standalone_assignment(data, asset, user, business_id)
        if proof is not None:
            return
        raise ProvisioningError('PRIVATE_ASSIGNMENT_TARGET_UNCONFIRMED',
            'Meta standalone permissions relation is unconfirmed: ' + reason + '. No assignment was sent.', retryable=True)
    found_asset, found_user = False, False
    def walk(value):
        nonlocal found_asset, found_user
        if isinstance(value, dict):
            kind = str(value.get('__typename') or '').lower()
            if kind in {'page', 'adaccount', 'businessasset', 'businessconnectedobject'}:
                found_asset |= asset in {_id(value.get(k)) for k in ('id', 'assetID', 'business_object_id', 'business_object_ui_id')}
            if kind in {'businessuser', 'businessscopeduser', 'businessscopeduserorrequest', 'adbusinessuser'}:
                found_user |= _id(value.get('id')) == user
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data)
    if not (found_asset and found_user):
        raise ProvisioningError('PRIVATE_ASSIGNMENT_TARGET_UNCONFIRMED',
            'Meta rights read did not return the exact asset and Business user. No assignment was sent.', retryable=True)


def full_control_tasks(config, asset_type, *, variant=None):
    rows = config.get("assetConfigs") or []
    matches = [row for row in rows if isinstance(row, dict) and row.get("assetType") == asset_type and row.get("hasUserPermissions") is True]
    if len(matches) != 1:
        raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta did not confirm a unique permission configuration for " + asset_type, retryable=True)
    definitions = matches[0].get("permissionTasksConfig") or []
    if not isinstance(definitions, list) or any(not isinstance(row, dict) or not _id(row.get("taskID")) for row in definitions):
        raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta returned malformed permission tasks.", retryable=True)
    table = {_id(row.get("taskID")): row for row in definitions}
    if len(table) != len(definitions):
        raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta returned conflicting permission tasks.", retryable=True)
    allowed = set(table)
    variant_config = matches[0].get("assetVariantConfig")
    if matches[0].get("hasAssetVariants") is True and "assetVariantConfig" not in matches[0]:
        raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta omitted the asset variant configuration field.", retryable=True)
    if variant_config is not None:
        if not isinstance(variant_config, dict):
            raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta returned a malformed asset variant configuration.", retryable=True)
        variants = variant_config.get("assetVariantPermissionConfig") or []
        if not isinstance(variants, list):
            raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta returned a malformed variant permission list.", retryable=True)
        selected = [row for row in variants if isinstance(row, dict) and variant is not None and row.get("assetVariantName") == variant]
        if len(selected) != 1:
            raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta did not confirm this asset's permission variant.", retryable=True)
        if "availablePermissionTaskIDsForVariant" not in selected[0]:
            raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta omitted the variant task restriction field.", retryable=True)
        restriction = selected[0]["availablePermissionTaskIDsForVariant"]
        # The observed Meta SDK treats an explicit null as unrestricted;
        # an empty list means no available tasks. Omission is inconclusive.
        allowed = set(table) if restriction is None else _tasks(restriction)
        if (restriction is not None and (not isinstance(restriction, list) or len(allowed) != len(restriction))) or not allowed.issubset(table):
            raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta returned incomplete variant permission tasks.", retryable=True)
    tasks = {key for key, row in table.items() if key in allowed and row.get("taskPermissionType") == "FULL_CONTROL_TASK"}
    if not tasks:
        raise ProvisioningError("PRIVATE_FULL_CONTROL_TASKS_UNAVAILABLE", "Meta did not expose full-control tasks for " + asset_type, retryable=True)
    pending = list(tasks)
    while pending:
        key = pending.pop()
        # BizKitSettingsConfigProvider normalizes a nullable impliedTaskIDs
        # scalar to [], before any permission controls consume it.
        raw_implied = table[key].get("impliedTaskIDs")
        if raw_implied is None:
            raw_implied = []
        implied = _tasks(raw_implied)
        if not isinstance(raw_implied, list) or len(implied) != len(raw_implied):
            raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta returned malformed implied permission tasks.", retryable=True)
        # Meta filters visible task controls by variant, but the implication
        # helper adds explicit implied IDs even when they have no visible control.
        # Missing controls simply have no further implications. Reject malformed
        # IDs above, not a valid dependency declared by an allowed full-control task.
        for implied_id in implied - tasks:
            tasks.add(implied_id)
            if implied_id in allowed:
                pending.append(implied_id)
    return sorted(tasks)


def response_shape(payload):
    output = []
    def walk(value, path="data", depth=0):
        if depth > 7 or len(output) >= 60:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if any(word in key.lower() for word in ("token", "cookie", "auth", "password", "dtsg", "lsd")):
                    continue
                output.append(path + "." + key + ":" + type(child).__name__)
                walk(child, path + "." + key, depth + 1)
        elif isinstance(value, list) and value:
            walk(value[0], path + "[]", depth + 1)
    walk((payload or {}).get("data", {}))
    return output


def permission_config_shape(config, asset_type, variant):
    """Only permission schema metadata; never include the complete config."""
    asset_configs = config.get('assetConfigs')
    rows = [row for row in (asset_configs if isinstance(asset_configs, list) else [])
        if isinstance(row, dict) and row.get('assetType') == asset_type]
    result = {'asset_type': asset_type, 'variant': str(variant)[:80] if variant is not None else None,
        'matching_configs': len(rows)}
    if len(rows) == 1:
        row = rows[0]
        tasks = row.get('permissionTasksConfig')
        variants = row.get('assetVariantConfig')
        result.update(task_config_type=type(tasks).__name__, has_user_permissions=row.get('hasUserPermissions') is True,
            has_asset_variants=row.get('hasAssetVariants') is True,
            variant_config_present='assetVariantConfig' in row, variant_config_type=type(variants).__name__)
        result['task_shapes'] = [{'id': _id(task.get('taskID')), 'kind': str(task.get('taskPermissionType'))[:80],
            'implied_type': type(task.get('impliedTaskIDs')).__name__,
            'implied_count': len(task['impliedTaskIDs']) if isinstance(task.get('impliedTaskIDs'), list) else None}
            for task in (tasks if isinstance(tasks, list) else [])[:100] if isinstance(task, dict)]
        variant_rows = variants.get('assetVariantPermissionConfig') if isinstance(variants, dict) else []
        result['variant_shapes'] = [{'name': str(item.get('assetVariantName'))[:80],
            'restriction_present': 'availablePermissionTaskIDsForVariant' in item,
            'restriction_type': type(item.get('availablePermissionTaskIDsForVariant')).__name__}
            for item in (variant_rows if isinstance(variant_rows, list) else [])[:30]
            if isinstance(item, dict)]
    return result


async def ensure_private_page_full_control(web, *, page_id, business_id, ad_account_id,
                                           profile_id, checkpoint, prior, rk_asset_id=""):
    business, page, account = _id(business_id), _id(page_id), _id(ad_account_id)
    profile = getattr(web, "profile", None)
    uid = _id((getattr(profile, "cookies", {}) or {}).get("c_user"))
    if not all((business, page, account, uid)) or business == uid:
        raise ProvisioningError("PRIVATE_PAGE_ACCESS_TARGET_INVALID", "Exact profile, BM, Page and RK identities are required.")
    bootstrap = await web.bootstrap()
    if str(getattr(bootstrap, "actor_id", "")) != uid:
        raise ProvisioningError("SESSION_EXPIRED", "The HTTP actor does not match the selected profile.", retryable=True)
    observed = StaticAssetContracts()
    await checkpoint({"activity": "PAGE_ACCESS_STATIC_CONTRACT_READY", "transport": "private_http",
        "contract_revision": contract_metadata("CLAIM")["revision"], "browser_started": False})

    async def read(friendly, variables):
        command = observed.query(friendly, variables)
        if command is None:
            raise ProvisioningError("PRIVATE_ASSET_READ_CONTRACT_UNAVAILABLE", "Pinned Meta query schema is unavailable: " + friendly, retryable=True)
        log.info('[%s] static_contract operation=%s doc_id=%s revision=%s kind=query browser_started=False',
            profile_id, friendly, command['doc_id'], contract_metadata('CONFIG')['revision'])
        payload = await web.graphql(command["doc_id"], command["variables"], friendly_name=friendly,
            endpoint_url="https://business.facebook.com/api/graphql/", business_context_id=business)
        _data(payload)
        return payload

    payload = await read(CONFIG, {"businessID": business})
    node = _data(payload).get("business")
    if not isinstance(node, dict) or _id(node.get("id")) != business or node.get("scheduledForDeletion") is True:
        raise ProvisioningError("PRIVATE_BM_TARGET_UNCONFIRMED", "Meta did not confirm the exact active BM for asset assignment.", retryable=True)
    viewer = node.get("businessUser")
    user = _id(viewer.get("id")) if isinstance(viewer, dict) else ""
    if not user:
        raise ProvisioningError("PRIVATE_BUSINESS_USER_UNCONFIRMED", "Meta did not confirm the current profile's scoped Business user. Facebook UID is not used as a substitute.", retryable=True)
    config = node.get("bizKitSettingsConfig")
    if not isinstance(config, dict):
        raise ProvisioningError("PRIVATE_FULL_CONTROL_CONFIG_UNCONFIRMED", "Meta did not return current asset permission tasks.", retryable=True)
    async def resolve_tasks(asset_type, variant=None):
        try:
            tasks = full_control_tasks(config, asset_type, variant=variant)
        except ProvisioningError as exc:
            diagnostic = {'stage': 'private_permission_config', 'code': exc.code,
                'reason': str(exc), **permission_config_shape(config, asset_type, variant)}
            await checkpoint({'diagnostic': diagnostic})
            log.info('[%s] PAGE_ACCESS permission_config rejected=%s', profile_id,
                json.dumps(diagnostic, separators=(',', ':')))
            raise
        log.info('[%s] PAGE_ACCESS permission_config asset=%s full_task_count=%d', profile_id, asset_type, len(tasks))
        return tasks
    rk_tasks = await resolve_tasks("AD_ACCOUNT")
    asset_types = sorted({row["assetType"] for row in config.get("assetConfigs", []) if isinstance(row, dict)
        and row.get("hasUserPermissions") is True and isinstance(row.get("assetType"), str)})
    target = {"business_id": business, "page_id": page, "ad_account_id": account, "rk_asset_id": _id(rk_asset_id) or account,
        "operator_uid": uid, "business_user_id": user}
    previous = prior.get("private_target") or {}
    if previous and previous != target:
        raise ProvisioningError("PRIVATE_PAGE_ACCESS_CHECKPOINT_MISMATCH", "The retained Page access operation belongs to another target or Business user.")
    operations = dict(prior.get("private_operations") or {})

    async def save(key, status, **extra):
        operations[key] = {**operations.get(key, {}), "status": status, **extra}
        await checkpoint({"phase": "PRIVATE_ASSET_" + status, "access_mode": "existing_page_full_control",
            "private_target": target, "private_operations": dict(operations), "transport": "private_http", "browser_started": False})
        log.info("[%s] PAGE_ACCESS private operation=%s phase=%s business=%s page=%s rk=%s", profile_id, key, status, business, page, account)

    async def submit(key, friendly, variables, verify):
        if operations.get(key, {}).get("status") in _PENDING:
            raise ProvisioningError("PRIVATE_ASSET_RESULT_UNKNOWN", "Previous " + key + " submit is retained; fresh verification is inconclusive. No duplicate POST was sent.", retryable=True)
        command = observed.mutation(friendly, variables)
        if command is None:
            raise ProvisioningError("PRIVATE_ASSET_MUTATION_CONTRACT_UNAVAILABLE", "Pinned Meta contract does not certify the exact " + key + " variables. No mutation was sent.", retryable=True)
        sent = False
        async def before_submit():
            nonlocal sent
            await save(key, "SUBMIT_INTENT")
            sent = True
        try:
            log.info('[%s] static_contract operation=%s doc_id=%s revision=%s kind=mutation browser_started=False',
                profile_id, friendly, command['doc_id'], contract_metadata('CLAIM')['revision'])
            response = await web.graphql(command["doc_id"], command["variables"], friendly_name=friendly,
                endpoint_url="https://business.facebook.com/api/graphql/", business_context_id=business, before_submit=before_submit)
            rejected = isinstance(response, dict) and bool(response.get("errors") or response.get("error")) and not response.get("data")
            if rejected:
                await save(key, "REJECTED")
                raise ProvisioningError("PRIVATE_ASSET_REQUEST_REJECTED", "Meta rejected " + key + "; no verified ownership or rights were recorded.", retryable=False)
            await save(key, "RESULT_UNVERIFIED")
        except ProvisioningError:
            raise
        except Exception as exc:
            rejected = getattr(exc, "request_may_have_been_sent", None) is False
            payload = getattr(exc, "payload", getattr(exc, "meta_payload", {}))
            rejected = rejected or (isinstance(payload, dict) and bool(payload.get("errors") or payload.get("error")) and not payload.get("data"))
            if sent:
                await save(key, "REJECTED" if rejected else "RESULT_UNKNOWN")
            if not sent or rejected:
                code = getattr(exc, "code", "PRIVATE_ASSET_NOT_SUBMITTED")
                raise ProvisioningError(code, "Meta asset operation did not complete; " + key + " remains unconfirmed.", retryable=bool(getattr(exc, "retryable", True))) from exc
        for attempt in range(3):
            proof = await verify()
            if proof:
                await save(key, "CONFIRMED", proof=proof)
                return proof
            if attempt < 2:
                await asyncio.sleep(0.4 * (attempt + 1))
        raise ProvisioningError("PRIVATE_ASSET_RESULT_UNKNOWN", "Meta did not independently confirm " + key + ". The submitted operation is retained.", retryable=True)

    page_variant = None
    async def ownership():
        nonlocal page_variant
        payload = await read(PAGE, {"businessID": business, "pageID": page, "isMMAPageClaim": False, "isMMAPageTransfer": False})
        node = _data(payload).get("page")
        if not isinstance(node, dict) or _id(node.get("id")) != page or "ownerBusiness" not in node:
            raise ProvisioningError("PRIVATE_PAGE_OWNERSHIP_INCONCLUSIVE", "Exact Page ownership was not returned by Meta.", retryable=True)
        page_variant = node.get("business_object_asset_type_variant")
        owner = node["ownerBusiness"]
        if owner is not None and (not isinstance(owner, dict) or not _id(owner.get("id"))):
            raise ProvisioningError("PRIVATE_PAGE_OWNERSHIP_INCONCLUSIVE", "Meta returned an incomplete Page owner.", retryable=True)
        owner_id = _id(owner.get("id")) if isinstance(owner, dict) else ""
        if owner_id and owner_id != business:
            raise ProvisioningError("PAGE_OWNED_BY_ANOTHER_BUSINESS", "This Page is already owned by BM " + owner_id + ". Add existing Page cannot give a second BM ownership.")
        if owner_id == business:
            return {"page_id": page, "owner_business_id": business}
        if "permission_to_claim_to_business" not in node or node.get("permission_to_claim_to_business") == "REJECTED":
            raise ProvisioningError("PRIVATE_PAGE_CLAIM_PERMISSION_UNCONFIRMED", "Meta did not confirm permission to add this existing Page.", retryable=True)
        return None

    owned = await ownership()
    page_tasks = await resolve_tasks("PAGE", page_variant)
    legacy_phase = str(prior.get("phase") or "")
    if not owned and legacy_phase in {"TARGET_PAGE_ACCESS_FULL_ADD_CLICK_INTENT", "TARGET_PAGE_ACCESS_FULL_ADD_SUBMITTED",
            "TARGET_PAGE_ACCESS_FULL_OWNER_APPROVE_CLICK_INTENT", "TARGET_PAGE_ACCESS_FULL_OWNER_APPROVE_SUBMITTED"}:
        raise ProvisioningError("PRIVATE_ASSET_RESULT_UNKNOWN", "Previous Page ownership submit is retained; fresh HTTP verification is inconclusive.", retryable=True)
    if not owned:
        owned = await submit("claim_page", CLAIM, {"businessID": business, "pageID": page, "igAuthCode": None, "igOIDCToken": "",
            "shouldRemoveDirectUsersBeforeClaiming": "KEEP", "claimingEntryPoint": observed.claim_entrypoint(), "selectedFBUserID": None,
            "isMMAPageTransfer": False, "qplJoinID": str(uuid.uuid4())}, ownership)
    else:
        await save("claim_page", "CONFIRMED", proof=owned)

    proofs = {}
    for key, asset, tasks in (("assign_page", page, page_tasks), ("assign_rk", _id(rk_asset_id) or account, rk_tasks)):
        async def verify(asset=asset, tasks=tasks):
            payload = await read(RIGHTS, {"assetID": asset, "businessID": business, "userID": user, "surface": "LWI"})
            diagnostic = {"stage": key + "_verification", "response_shape": response_shape(payload)}
            try:
                _assert_assignment_targets(payload, asset, user, business)
            except ProvisioningError as exc:
                await checkpoint({"diagnostic": {**diagnostic, "code": exc.code}})
                log.info('[%s] PAGE_ACCESS rights_read rejected=%s', profile_id,
                    json.dumps({**diagnostic, 'code': exc.code}, separators=(',', ':')))
                raise
            proof = assignment_proof(payload, asset_id=asset, user_id=user, required_tasks=tasks, business_id=business)
            if not proof:
                if 'business_object_rendered_in_ui' in _data(payload):
                    assignment, reason = _standalone_assignment(_data(payload), asset, user, business)
                    assigned = set(assignment['assigned_task_ids']) if assignment else set()
                    diagnostic['assignment_check'] = {'relation_confirmed': assignment is not None,
                        'reason': reason, 'required_count': len(tasks), 'assigned_count': len(assigned),
                        'missing_task_ids': sorted(set(tasks) - assigned)}
                await checkpoint({"diagnostic": diagnostic})
                log.info('[%s] PAGE_ACCESS rights_read unconfirmed=%s', profile_id,
                    json.dumps(diagnostic, separators=(',', ':')))
            return proof
        proof = await verify()
        if not proof:
            proof = await submit(key, ASSIGN, {"businessID": business, "userID": user, "assetID": asset,
                "taskIDs": tasks, "assetTypes": ["PAGE"] if key == "assign_page" else asset_types}, verify)
        else:
            await save(key, "CONFIRMED", proof=proof)
        proofs[key] = proof
    return {"page_id": page, "business_id": business, "ad_account_id": account,
        "page_owned_by_business": True, "page_shared_to_business": True, "operator_ads_access_assigned": True,
        "operator_full_control_verified": True, "rk_operator_full_control_verified": True,
        "operator_full_control_proof": proofs["assign_page"], "rk_operator_full_control_proof": proofs["assign_rk"],
        "access_mode": "existing_page_full_control", "transport": "private_http", "browser_started": False}
