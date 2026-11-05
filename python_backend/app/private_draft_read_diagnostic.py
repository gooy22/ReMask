"""Bounded relationship observations from an existing immutable draft intent.

This does not confirm a draft, alter its journal, or dispatch advertising POSTs.
Only exact handles already retained by the original job can be read.
"""
from __future__ import annotations

import json
import logging
import re

from .private_ads_draft import fail, fragment_parent_matches, fragment_values_match
from .private_ads_http import AdsManagerPrivateHttp
from .private_ads_inspect import _validation_fields, adapter_scope, saved_draft_intent, validation_diagnostic
from .private_ads_draft_fallback import read_fragment_with_account_object_fallback


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


_SENSITIVE_SHAPE_KEY = re.compile(r"cookie|token|secret|password|credential|authorization|dtsg|lsd|session|url|text|message", re.I)


def safe_nested_shape(value, *, depth=0, budget=None):
    """Bounded value shape for reviewed unexpected Meta defaults; never emit free text."""
    if budget is None:
        budget = {"nodes": 0}
    budget["nodes"] += 1
    result = scalar_shape(value)
    if depth >= 5 or budget["nodes"] > 64:
        return {**result, "truncated": True}
    if isinstance(value, dict):
        safe_items = [(key, item) for key, item in value.items()
                      if isinstance(key, str) and not _SENSITIVE_SHAPE_KEY.search(key)]
        result["keys"] = sorted(key for key, _ in safe_items)[:32]
        result["values"] = {key: safe_nested_shape(item, depth=depth + 1, budget=budget)
                            for key, item in safe_items[:32]}
        if len(safe_items) > 32:
            result["truncated"] = True
    elif isinstance(value, list):
        result["length"] = len(value)
        result["items"] = [safe_nested_shape(item, depth=depth + 1, budget=budget)
                           for item in value[:8]]
        if len(value) > 8:
            result["truncated"] = True
    elif isinstance(value, str) and len(value) <= 16000:
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            result["json_decoded"] = safe_nested_shape(decoded, depth=depth + 1, budget=budget)
    return result


_SAFE_REVIEWED_ENUM_FIELDS = frozenset({"automation_unified_campaign_type", "ad_creation_package_config"})
_SAFE_REVIEWED_ENUM_VALUE = re.compile(r"[A-Za-z0-9_:-]{1,64}\Z")


def safe_reviewed_value_shape(field, value):
    """Expose only bounded scalar enum defaults for two reviewed Meta-owned fields."""
    result = safe_nested_shape(value)
    if field not in _SAFE_REVIEWED_ENUM_FIELDS:
        return result
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            return result
    if isinstance(decoded, str) and _SAFE_REVIEWED_ENUM_VALUE.fullmatch(decoded):
        result["json_scalar"] = {"type": "str", "length": len(decoded), "value": decoded}
    elif decoded is None or type(decoded) in {bool, int}:
        result["json_scalar"] = scalar_shape(decoded)
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
    result["unexpected_value_shapes"] = [
        {"field": row["field"], "new_value": safe_reviewed_value_shape(row["field"], row.get("new_value"))}
        for row in rows if isinstance(row, dict)
        and row.get("field") in result["unexpected_reviewed_fields"]
    ][:20]
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
    target, _key, _fingerprint, actor, source_digest, draft_id, fragments, pending = await saved_draft_intent(
        adapter, profile_id=profile_id, context=context, payload=payload)
    if draft_id is None:
        raise fail("RECONCILE_REQUIRED", "Saved draft has no exact read handle")
    try:
        web = await session.facebook_web()
        client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
            business_id=target["business_id"], actor_id=actor, reference_read=True)
    except Exception as exc:
        from fb_worker import RemoteRequestError
        import httpx
        if isinstance(exc, (RemoteRequestError, httpx.HTTPError, TimeoutError)):
            stage = getattr(exc, "transport_stage", "") or "connect"
            logging.getLogger(__name__).warning(
                "saved_draft_read_transport_failure profile=%s stage=%s error_type=%s",
                profile_id, stage, type(exc).__name__)
            raise fail("READ_TRANSPORT_UNAVAILABLE",
                "The saved Meta draft could not be read because the profile connection failed before a response",
                True) from None
        raise
    client.read_only = True
    draft = await client.get("/addraft_" + draft_id,
        {"fields": ["id", "account_id", "author_id", "ownership_type"]})
    adapter_scope(draft, target["ad_account_id"])
    if draft.get("id") != draft_id or draft.get("author_id") != actor or draft.get("ownership_type") != "USER":
        raise fail("READ_SCOPE_MISMATCH", "Saved draft ownership was not confirmed")
    reads, parent = [], None
    campaign_object_read, account_object_fallbacks = None, []
    for level, fragment_id, values, confirmed_object in fragments:
        observed_result = await read_fragment_with_account_object_fallback(
            client, account_id=target["ad_account_id"], draft_id=draft_id,
            fragment_id=fragment_id, level=level, values=values,
            parent_id=parent, object_id=confirmed_object, allow_fallback=True)
        fallback = observed_result["fallback"]
        if fallback is not None:
            account_object_fallbacks.append(fallback)
            if level == "campaign":
                campaign_object_read = fallback
            checks = {
                "fragment_receipt": fallback.get("fragment_id") == fragment_id,
                "draft_id": fallback.get("draft_id") == draft_id,
                "account_id": fallback.get("account_id") == target["ad_account_id"],
                "level": fallback.get("level") == level,
                "parent": level == "campaign" or (parent is not None and values.get("campaign_id") == parent),
            }
            observed = {
                "id": {"present": False, **scalar_shape(None)},
                "ad_draft_id": {"present": True, **scalar_shape(draft_id)},
                "ad_object_id": {"present": True, **scalar_shape(fallback.get("object_id"))},
                "ad_object_type": {"present": True, **scalar_shape(level)},
                "parent_ad_object_id": {"present": False, **scalar_shape(None)},
                "validation_status": {"present": False, **scalar_shape(None)},
                "status": {"present": True, **scalar_shape("PAUSED")},
            }
            reads.append({"level": level, "fragment_id": fragment_id,
                "relationships_match": all(checks.values()), "checks": checks,
                "observed": observed, "settings_match": fallback.get("settings_match") is True,
                "settings_diagnostic": None,
                "active_error_diagnostic": validation_diagnostic(None, observed=False),
                "account_object_read": fallback})
            if not all(checks.values()):
                break
            parent = fallback.get("object_id")
            continue
        row = observed_result["row"]
        adapter_scope(row, target["ad_account_id"])
        checks = {"id": row.get("id") == fragment_id,
            "ad_draft_id": row.get("ad_draft_id") == draft_id,
            "ad_object_type": row.get("ad_object_type") == level,
            "parent_ad_object_id": fragment_parent_matches(row.get("parent_ad_object_id"), level=level,
                parent_id=parent, root_account_id=getattr(client, "draft_root_parent_id", client.context.account_id))}
        settings_match = fragment_values_match(row.get("values"), values, business_id=target["business_id"])
        settings_diagnostic = values_diagnostic(row.get("values"), values, target["business_id"])
        if not settings_match:
            logging.getLogger(__name__).warning(
                "saved_draft_read_settings_mismatch profile=%s level=%s source_digest=%s diagnostic=%s",
                profile_id, level, source_digest,
                json.dumps(settings_diagnostic, sort_keys=True, separators=(",", ":")))
        reads.append({"level": level, "fragment_id": fragment_id,
            "relationships_match": all(checks.values()), "checks": checks,
            "observed": {field: {"present": field in row, **scalar_shape(row.get(field))}
                for field in ("id", "ad_draft_id", "ad_object_id", "ad_object_type",
                              "parent_ad_object_id", "validation_status", "status")},
            "settings_match": settings_match,
            "settings_diagnostic": settings_diagnostic,
            "active_error_diagnostic": validation_diagnostic(row.get("active_errors"), observed="active_errors" in row)})
        if not all(checks.values()):
            break
        parent = row.get("ad_object_id")
    validation_summary = [
        {"level": read["level"], "validation_status": read["observed"].get("validation_status"),
         "active_errors": read["active_error_diagnostic"]}
        for read in reads
    ]
    logging.getLogger(__name__).info(
        "saved_draft_read_validation profile=%s draft_id=%s fragments=%s",
        profile_id, draft_id, json.dumps(validation_summary, sort_keys=True, separators=(",", ":")))
    return {"draft_id": draft_id, "fragment_reads": reads, "pending_stage": pending,
        "campaign_object_read": campaign_object_read,
        "account_object_read_fallbacks": account_object_fallbacks,
        "draft_verified": False, "advertising_mutation_posts": 0, "persisted": False,
        "transport": "private_ads_manager_http_h2", "source": "independent_private_ads_http"}
