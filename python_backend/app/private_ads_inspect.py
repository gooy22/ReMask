"""Fresh reads of a saved draft intent, without replaying any draft operation."""
from __future__ import annotations

import copy
import re

from .private_ads_draft import FRAGMENT_FIELDS, LEVELS, fail, fragment_values_match, identity
from .private_ads_http import AdsManagerPrivateHttp
from .private_launch import _launch_fingerprint

# Exact reviewed sender at main 2f5444324d8e. This permits inspection only;
# it does not authorize a current sender to resume an old mutation.
READ_ONLY_DRAFT_PREDECESSORS = frozenset({
    "f9f8e21841da2cf174bc516941a5581fe1c5b65642783ab84ed2b03ede68c218",
})


async def inspect_saved_draft(adapter, *, profile_id, context, session, payload):
    if payload.get("reconcile_only") is not True:
        raise fail("INVALID_INPUT", "Saved draft inspection must be explicitly read-only")
    target = {"profile_id": str(profile_id), **{key: identity(payload.get(key))
              for key in ("business_id", "ad_account_id", "page_id")}}
    key = payload.get("launch_key")
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise fail("INVALID_INPUT", "Inspection requires the original saved Launch key")
    if adapter.owner._inventory_row(context, target["business_id"], target["ad_account_id"]) is None:
        raise fail("ACCOUNT_CONTEXT_UNAVAILABLE", "Saved draft account is absent from this profile inventory")
    fingerprint = _launch_fingerprint(payload, target["page_id"])
    actor = identity(str(context.cookies.get("c_user") or ""))
    await adapter.owner.state.init()
    rows = await adapter.owner.state.all_steps(key)
    saved = {row["step"]: row for row in rows}
    required = {"ADS_MANAGER:DRAFT", *("ADS_MANAGER:FRAGMENT_" + level.upper() for level in LEVELS)}
    if not required <= set(saved) or "ADS_MANAGER:PUBLISH" in saved:
        raise fail("RECONCILE_REQUIRED", "Inspection requires the saved unpublished draft hierarchy; no POST was sent")
    sources = set()
    for row in rows:
        evidence = row.get("result", {}).get("evidence", {})
        source = evidence.get("source_digest")
        if (not row["step"].startswith("ADS_MANAGER:")
                or any(str(row.get(field)) != target[field] for field in ("profile_id", "business_id", "ad_account_id"))
                or evidence.get("actor_id") != actor or evidence.get("launch_fingerprint") != fingerprint
                or not isinstance(source, str) or not re.fullmatch(r"[a-f0-9]{64}", source)):
            raise fail("SAVED_OPERATION_MISMATCH", "Inspection target, actor or configuration differs from the saved intent")
        sources.add(source)
    if len(sources) != 1 or not sources <= {adapter.source_digest, *READ_ONLY_DRAFT_PREDECESSORS}:
        raise fail("SAVED_OPERATION_MISMATCH", "Saved draft sender has not been reviewed for read-only inspection")
    draft = saved["ADS_MANAGER:DRAFT"].get("result", {}).get("evidence", {}).get("receipt", {})
    draft_id = identity(draft.get("draft_id"))
    fragments = []
    for level in LEVELS:
        evidence = saved["ADS_MANAGER:FRAGMENT_" + level.upper()].get("result", {}).get("evidence", {})
        fragment_id = identity(evidence.get("receipt", {}).get("fragment_id"))
        values = evidence.get("binding", {}).get("values")
        if not isinstance(values, dict) or not values:
            raise fail("RECONCILE_REQUIRED", "A saved fragment has no exact submitted field binding")
        confirmed_object = evidence.get("verification", {}).get("ad_object_id")
        if confirmed_object is not None:
            identity(confirmed_object)
        fragments.append((level, fragment_id, copy.deepcopy(values), confirmed_object))
    web = await session.facebook_web()
    client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
        business_id=target["business_id"], actor_id=actor)
    client.read_only = True
    account = await client.get("/act_" + target["ad_account_id"], {"fields": ["id", "account_id", "business"]})
    if (not isinstance(account.get("id"), str) or account["id"].removeprefix("act_") != target["ad_account_id"]
            or not isinstance(account.get("business"), dict) or account["business"].get("id") != target["business_id"]):
        raise fail("READ_SCOPE_MISMATCH", "Fresh account read did not confirm the saved Business")
    draft = await client.get("/addraft_" + draft_id, {"fields": ["id", "account_id", "author_id", "ownership_type"]})
    adapter_scope(draft, target["ad_account_id"])
    if draft.get("id") != draft_id or draft.get("author_id") != actor or draft.get("ownership_type") != "USER":
        raise fail("READ_SCOPE_MISMATCH", "Fresh draft ownership differs from the saved intent")
    objects, reads = {}, []
    for level, fragment_id, values, confirmed_object in fragments:
        row = await client.get("/addraftfragment_" + fragment_id, {"fields": FRAGMENT_FIELDS})
        adapter_scope(row, target["ad_account_id"])
        parent = objects.get("campaign" if level == "ad_set" else "ad_set") if level != "campaign" else None
        if (row.get("id") != fragment_id or row.get("ad_draft_id") != draft_id or row.get("ad_object_type") != level
                or parent is not None and row.get("parent_ad_object_id") != parent):
            raise fail("READ_RELATION_MISMATCH", "Fresh fragment relationships differ from the saved draft")
        if level != "campaign" and values.get("campaign_id") != objects["campaign"]:
            raise fail("READ_RELATION_MISMATCH", "Saved Ad Set or Ad has another Campaign relationship")
        if level == "ad" and values.get("adset_id") != objects["ad_set"]:
            raise fail("READ_RELATION_MISMATCH", "Saved Ad has another Ad Set relationship")
        if not fragment_values_match(row.get("values"), values, business_id=target["business_id"]):
            raise fail("READ_SPEC_MISMATCH", "Fresh draft values differ from the original submitted fields")
        objects[level] = identity(row.get("ad_object_id"))
        if confirmed_object is not None and objects[level] != confirmed_object:
            raise fail("READ_RELATION_MISMATCH", "Fresh fragment refers to another previously confirmed object")
        if len(set(objects.values())) != len(objects):
            raise fail("READ_RELATION_MISMATCH", "Fresh draft levels have duplicate object identities")
        reads.append({"level": level, "fragment_id": fragment_id, "ad_object_id": objects[level],
            "submitted_values": values, "settings_match": True,
            "fresh_values": {entry["field"]: copy.deepcopy(entry.get("new_value")) for entry in row["values"]},
            "validation_status": row.get("validation_status"), "active_errors_present": bool(row.get("active_errors")),
            "publish_status": row.get("publish_status"), "status": row.get("status")})
    # The job result persists this evidence. Original mutation receipts and
    # fingerprints remain untouched, even if Meta reports a validation error.
    return {"status": "SUCCESS", "execution_mode": "DRAFT_INSPECT", **target, "launch_key": key,
        "draft_id": draft_id, "draft_objects": objects, "fragment_reads": reads,
        "fragment_storage_verified": True, "relationships_confirmed": True,
        "editor_verified": False, "published": None, "payment_checked_live": False,
        "mutation_posts": 0, "source": "independent_private_ads_http", "recovered_source_digest": next(iter(sources))}


def adapter_scope(row, account):
    if (not isinstance(row, dict) or row.get("error") or row.get("errors")
            or not isinstance(row.get("account_id"), str) or row["account_id"].removeprefix("act_") != account):
        raise fail("READ_SCOPE_MISMATCH", "Fresh draft read returned another advertising account")
