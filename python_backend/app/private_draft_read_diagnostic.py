"""Bounded relationship observations from an existing immutable draft intent.

This does not confirm a draft, alter its journal, or dispatch advertising POSTs.
Only exact handles already retained by the original job can be read.
"""
from __future__ import annotations

import json
import re

from .private_ads_draft import FRAGMENT_FIELDS, fail, fragment_parent_matches, fragment_values_match
from .private_ads_http import AdsManagerPrivateHttp
from .private_ads_inspect import _validation_fields, adapter_scope, saved_draft_intent, validation_diagnostic


def scalar_shape(value):
    result = {"type": type(value).__name__}
    if value is None or type(value) is bool:
        result["value"] = value
    elif type(value) is int and 0 <= value < 10 ** 40:
        result["value"] = value
    elif isinstance(value, str):
        result["length"] = len(value)
        if (value in {"", "campaign", "ad_set", "ad", "VALIDATED", "HAS_ERRORS", "PENDING", "PAUSED", "ACTIVE",
                      "AUCTION", "LINK_CLICKS", "OUTCOME_TRAFFIC"}
                or re.fullmatch(r"(?:(?:addraft|addraftfragment|act)_)?[0-9]{1,40}", value)):
            result["value"] = value
    return result


def values_diagnostic(rows, expected, business_id):
    """Explain the existing strict comparison without returning ad text or secrets."""
    result = {"collection": scalar_shape(rows), "fields": []}
    if not isinstance(rows, list) or len(rows) > 101:
        return result
    wanted = dict(expected)
    if any(isinstance(row, dict) and row.get("field") == "acting_business_id" for row in rows):
        wanted["acting_business_id"] = business_id
    result["row_count"] = len(rows)
    result["unexpected_row_count"] = sum(not isinstance(row, dict)
        or not isinstance(row.get("field"), str) or row["field"] not in wanted for row in rows)
    result["unexpected_reviewed_fields"] = sorted({row["field"] for row in rows
        if isinstance(row, dict) and isinstance(row.get("field"), str)
        and row["field"] not in wanted and row["field"] in _validation_fields()})
    for field, value in sorted(wanted.items()):
        found = [row for row in rows if isinstance(row, dict) and row.get("field") == field]
        item = {"field": field, "count": len(found), "expected": scalar_shape(value),
                "matches": fragment_values_match(found, {field: value})}
        if len(found) == 1:
            actual = found[0].get("new_value")
            item["observed"] = scalar_shape(actual)
            if isinstance(actual, str) and len(actual) <= 16000:
                try:
                    item["json_decoded"] = scalar_shape(json.loads(actual))
                except (ValueError, RecursionError):
                    pass
        result["fields"].append(item)
    return result


async def read_diagnostic(adapter, *, profile_id, context, session, payload):
    target, _key, _fingerprint, actor, _source, draft_id, fragments, pending = await saved_draft_intent(
        adapter, profile_id=profile_id, context=context, payload=payload)
    if draft_id is None:
        raise fail("RECONCILE_REQUIRED", "Saved draft has no exact read handle")
    web = await session.facebook_web()
    client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
        business_id=target["business_id"], actor_id=actor, reference_read=True)
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
            "parent_ad_object_id": fragment_parent_matches(row.get("parent_ad_object_id"), level=level,
                parent_id=parent, root_account_id=getattr(client, "draft_root_parent_id", client.context.account_id))}
        reads.append({"level": level, "fragment_id": fragment_id,
            "relationships_match": all(checks.values()), "checks": checks,
            "observed": {field: {"present": field in row, **scalar_shape(row.get(field))}
                for field in ("id", "ad_draft_id", "ad_object_id", "ad_object_type",
                              "parent_ad_object_id", "validation_status", "status")},
            "settings_match": fragment_values_match(row.get("values"), values, business_id=target["business_id"]),
            "settings_diagnostic": values_diagnostic(row.get("values"), values, target["business_id"]),
            "active_error_diagnostic": validation_diagnostic(row.get("active_errors"), observed="active_errors" in row)})
        if not all(checks.values()):
            break
        parent = row.get("ad_object_id")
    return {"draft_id": draft_id, "fragment_reads": reads, "pending_stage": pending,
        "draft_verified": False, "advertising_mutation_posts": 0, "persisted": False,
        "transport": "private_ads_manager_http_h2", "source": "independent_private_ads_http"}
