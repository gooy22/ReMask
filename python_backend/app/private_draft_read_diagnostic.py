"""Bounded relationship observations from an existing immutable draft intent.

This does not confirm a draft, alter its journal, or dispatch advertising POSTs.
Only exact handles already retained by the original job can be read.
"""
from __future__ import annotations

import re

from .private_ads_draft import FRAGMENT_FIELDS, fail, fragment_values_match
from .private_ads_http import AdsManagerPrivateHttp
from .private_ads_inspect import adapter_scope, saved_draft_intent, validation_diagnostic


def scalar_shape(value):
    result = {"type": type(value).__name__}
    if value is None or type(value) is bool:
        result["value"] = value
    elif type(value) is int and 0 <= value < 10 ** 40:
        result["value"] = value
    elif isinstance(value, str):
        result["length"] = len(value)
        if (value in {"", "campaign", "ad_set", "ad", "VALIDATED", "HAS_ERRORS", "PENDING", "PAUSED", "ACTIVE"}
                or re.fullmatch(r"(?:(?:addraft|addraftfragment|act)_)?[0-9]{1,40}", value)):
            result["value"] = value
    return result


async def read_diagnostic(adapter, *, profile_id, context, session, payload):
    target, _key, _fingerprint, actor, _source, draft_id, fragments, pending = await saved_draft_intent(
        adapter, profile_id=profile_id, context=context, payload=payload)
    if draft_id is None:
        raise fail("RECONCILE_REQUIRED", "Saved draft has no exact read handle")
    web = await session.facebook_web()
    client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
        business_id=target["business_id"], actor_id=actor)
    client.read_only = True
    draft = await client.get("/addraft_" + draft_id,
        {"fields": ["id", "account_id", "author_id", "ownership_type"]})
    adapter_scope(draft, target["ad_account_id"])
    if draft.get("id") != draft_id or draft.get("author_id") != actor or draft.get("ownership_type") != "USER":
        raise fail("READ_SCOPE_MISMATCH", "Saved draft ownership was not confirmed")
    reads, parent = [], None
    for level, fragment_id, values, _confirmed_object in fragments:
        row = await client.get("/addraftfragment_" + fragment_id, {"fields": FRAGMENT_FIELDS})
        adapter_scope(row, target["ad_account_id"])
        checks = {"id": row.get("id") == fragment_id,
            "ad_draft_id": row.get("ad_draft_id") == draft_id,
            "ad_object_type": row.get("ad_object_type") == level,
            "parent_ad_object_id": row.get("parent_ad_object_id") in ((None, "") if parent is None else (parent,))}
        reads.append({"level": level, "fragment_id": fragment_id,
            "relationships_match": all(checks.values()), "checks": checks,
            "observed": {field: {"present": field in row, **scalar_shape(row.get(field))}
                for field in ("id", "ad_draft_id", "ad_object_id", "ad_object_type",
                              "parent_ad_object_id", "validation_status", "status")},
            "settings_match": fragment_values_match(row.get("values"), values, business_id=target["business_id"]),
            "active_error_diagnostic": validation_diagnostic(row.get("active_errors"), observed="active_errors" in row)})
        if not all(checks.values()):
            break
        parent = row.get("ad_object_id")
    return {"draft_id": draft_id, "fragment_reads": reads, "pending_stage": pending,
        "draft_verified": False, "advertising_mutation_posts": 0, "persisted": False,
        "transport": "private_ads_manager_http_h2", "source": "independent_private_ads_http"}
