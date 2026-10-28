"""Fresh Business-owned Page reads for unpublished drafts, never publication proof."""
from __future__ import annotations

import logging
import time

from .private_ads_catalog import identity
from .private_ads_draft import fail
from .private_page_ownership import _data, _id, assignment_proof, full_control_plan
from .static_meta_contracts import execute

log = logging.getLogger("remask_worker")


async def _actor(web):
    web.private_only = True
    actor = identity(str(web.profile.cookies.get("c_user") or ""))
    bootstrap = await web.bootstrap()
    if str(getattr(bootstrap, "actor_id", "")) != actor:
        raise fail("DRAFT_PAGE_ACTOR_UNVERIFIED", "Draft Page read did not confirm the selected profile actor")
    return actor


async def read_draft_pages(web, *, account, business, query=""):
    """Picker metadata only; Launch independently checks ownership and rights."""
    from .private_business_fan_page_create import read_pages
    # READ_FP is already profile-bound: FacebookWebSession.graphql verifies
    # its fresh bootstrap actor against this profile's c_user before sending
    # the exact-Business query. A preliminary _actor() bootstrap is redundant;
    # the Business GraphQL path discards verified profile bootstrap state and
    # downloads the document again. Keep the read private and bootstrap once.
    web.private_only = True
    rows, complete = await read_pages(web, business)
    if not complete or len(rows) > 500:
        raise fail("DRAFT_PAGE_INVENTORY_INCOMPLETE", "Exact Business Page inventory is incomplete", True)
    query = str(query).casefold()
    rows = [row for row in rows if not query or any(query in str(row[k]).casefold() for k in ("id", "name"))]
    return {"data": [{**row, "account_id": account, "publication_access_verified": False} for row in rows],
        "account_id": account, "business_id": business, "account_scope_verified": True,
        "checked_live": True, "checked_at": int(time.time()), "execution_mode": "DRAFT",
        "ad_account_page_access_verified": False, "source": "private_http_business_draft_pages"}


async def verify_draft_page(web, *, target, client):
    """Require current owner and effective full-control grants on Page and RK.

    No claim, assignment, billing, advertising publication or delivery request.
    The Settings UI asset ID is used only for the rights query; its returned
    business_object_id must match the exact selected Settings account. The
    advertising sender separately proves the Settings/UI alias relation.
    """
    actor = await _actor(web)
    business, page, account = (identity(target[k]) for k in ("business_id", "page_id", "ad_account_id"))
    if business == actor:
        raise fail("DRAFT_PAGE_BUSINESS_UNVERIFIED", "A Business portfolio is required")
    node = _data(await execute(web, "CONFIG", business=business)).get("business")
    if (not isinstance(node, dict) or _id(node.get("id")) != business
            or node.get("scheduledForDeletion") is True):
        raise fail("DRAFT_PAGE_BUSINESS_UNVERIFIED", "Draft read did not confirm the exact active Business")
    viewer = node.get("businessUser")
    user = _id(viewer.get("id")) if isinstance(viewer, dict) else ""
    if not user:
        raise fail("DRAFT_PAGE_OPERATOR_UNVERIFIED", "Current operator's scoped Business user is unconfirmed")
    config = node.get("bizKitSettingsConfig")
    if not isinstance(config, dict):
        raise fail("DRAFT_PAGE_OPERATOR_UNVERIFIED", "Current asset permission configuration is unconfirmed")
    page_node = _data(await execute(web, "PAGE", business=business, page=page)).get("page")
    owner = page_node.get("ownerBusiness") if isinstance(page_node, dict) else None
    if (not isinstance(page_node, dict) or _id(page_node.get("id")) != page
            or not isinstance(owner, dict) or _id(owner.get("id")) != business):
        raise fail("DRAFT_PAGE_OWNERSHIP_UNVERIFIED", "Selected Page is not freshly confirmed as owned by this Business")
    native = identity(getattr(client, "_native_account_id", account))
    proofs = {}
    for kind, asset, canonical, variant in (
            ("PAGE", page, page, page_node.get("business_object_asset_type_variant")),
            ("AD_ACCOUNT", native, account, None)):
        plan = full_control_plan(config, kind, variant=variant)
        response = await execute(web, "RIGHTS", business=business, asset=asset, user=user)
        proof = assignment_proof(response, asset_id=canonical, user_id=user, business_id=business,
            required_tasks=plan["required_task_ids"], permission_plan=plan)
        if not proof:
            raise fail("DRAFT_PAGE_OPERATOR_UNVERIFIED", "Fresh full-control permission is unconfirmed for " + kind)
        proofs[kind] = proof
    log.info("DRAFT_PAGE verified business=%s page=%s account=%s ownership=1 operator_full_control=1 publication_access=0 browser_started=False",
        business, page, account)
    return {"page_id": page, "owner_business_id": business, "operator_full_control_verified": True,
        "account_operator_full_control_verified": True, "checked_live": True,
        "source": "private_http_business_asset_rights", "publication_access_verified": False}
