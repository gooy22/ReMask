"""Ads Manager draft wire format from the captured October 12 sender.

This is a versioned private HTTP protocol, not a persisted Relay mutation.
Public source supplies the schema; a publication receipt is not object proof.
The helpers never request credentials, send requests, or activate advertisements.
"""
from __future__ import annotations

import copy
import json
import re

from .provisioning.models import ProvisioningError

LEVELS = ("campaign", "ad_set", "ad")
PUBLISH_FIELDS = ["async_request_set_id", "canceled_count", "error_count",
    "in_progress_count", "initial_count", "publish_error", "status",
    "success_count", "total_count", "fragment_statuses"]
FRAGMENT_FIELDS = ["account_id", "ad_draft_id", "ad_object_id", "ad_object_type",
    "id", "parent_ad_object_id", "publish_status", "status", "values"]


def fail(code, message, retryable=False):
    return ProvisioningError("PRIVATE_ADS_DRAFT_" + code, message, retryable=retryable)


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{4,39}", value):
        raise fail("INVALID_ID", "Advertising identity is missing or malformed")
    return value


def flatten_params(params):
    """Match captured flattenObject: omit null, JSON-encode non-strings."""
    if not isinstance(params, dict):
        raise fail("INVALID_INPUT", "Advertising parameters must be an object")
    try:
        return {key: value if isinstance(value, str) else
                json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                for key, value in params.items() if value is not None}
    except (TypeError, ValueError) as exc:
        raise fail("INVALID_INPUT", "Advertising parameters cannot be encoded") from exc


def fragment_params(draft_id, *, level, values, parent_id=None, object_id=None,
                    action="add", source="click_quick_create"):
    """Match getAPIParams and adsDraftEncodeFragmentValues without mutating input."""
    draft_id = identity(draft_id)
    if level not in LEVELS or action not in {"add", "modify"}:
        raise fail("INVALID_FRAGMENT", "Unsupported advertising fragment")
    if not isinstance(values, dict) or not values or len(values) > 100:
        raise fail("INVALID_FRAGMENT", "Advertising fragment fields are missing")
    if action == "add" and values.get("status") != "PAUSED":
        raise fail("INITIAL_STATUS", "New advertising fragments require an explicit PAUSED status")
    from .private_launch import PrivateLaunchContractStore
    PrivateLaunchContractStore._reject_auth_material(values)
    entries = []
    for field, value in sorted(values.items()):
        if not isinstance(field, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", field):
            raise fail("INVALID_FRAGMENT", "Advertising fragment field is malformed")
        if field == "status" and action == "add" and value != "PAUSED":
            raise fail("INITIAL_STATUS", "New advertising objects must be created PAUSED")
        entries.append({"field": field, "old_value": "null", "new_value":
            json.dumps(copy.deepcopy(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)})
    output = {"ad_draft_id": draft_id, "ad_object_type": level, "action": action,
              "source": source, "values": entries, "is_delete": False, "is_archive": False}
    if parent_id is not None:
        output["parent_ad_object_id"] = identity(parent_id)
    if object_id is not None:
        output["ad_object_id"] = identity(object_id)
    if action == "modify" and object_id is None:
        raise fail("INVALID_FRAGMENT", "An update requires its saved advertising object")
    return output


def publish_params(fragment_ids, caller_options=None):
    if not isinstance(fragment_ids, list) or not 1 <= len(fragment_ids) <= 100:
        raise fail("INVALID_FRAGMENT", "Publication requires distinct saved fragment IDs")
    ids = [identity(value) for value in fragment_ids]
    if len(set(ids)) != len(ids):
        raise fail("INVALID_FRAGMENT", "Publication requires distinct saved fragment IDs")
    output = {"fragments": ids, "ignore_errors": False, "include_fragment_statuses": True}
    if caller_options is not None:
        if (not isinstance(caller_options, dict) or set(caller_options) - {"ignore_errors", "include_fragment_statuses"}
                or any(type(value) is not bool for value in caller_options.values())):
            raise fail("INVALID_INPUT", "Publication caller options must be an object")
        from .private_launch import PrivateLaunchContractStore
        PrivateLaunchContractStore._reject_auth_material(caller_options)
        output.update(copy.deepcopy(caller_options))
    return output


def publication_status(response, *, operation_id, fragment_ids):
    """Accept only the saved operation and every expected fragment's success."""
    operation_id = identity(operation_id)
    expected = {identity(value) for value in fragment_ids}
    rows = response.get("data") if isinstance(response, dict) and not (
        response.get("error") or response.get("errors")) else None
    if not isinstance(rows, list) or len(rows) > 100:
        raise fail("PUBLISH_READ_INCOMPLETE", "Publication status response is incomplete", True)
    matching = [row for row in rows if isinstance(row, dict) and row.get("async_request_set_id") == operation_id]
    if len(matching) != 1:
        raise fail("PUBLISH_SCOPE_MISMATCH", "Publication returned no unique saved operation")
    row = matching[0]
    status = row.get("status")
    if status in {"PRE_PUBLISH", "IN_PROGRESS"}:
        return {"completed": False, "status": status, "operation_id": operation_id}
    if status == "FAILED" or row.get("publish_error"):
        raise fail("PUBLISH_FAILED", "Meta reported a failed publication")
    if status != "SUCCEEDED":
        raise fail("PUBLISH_STATUS_UNKNOWN", "Meta returned an unrecognized publication status")
    fragments = row.get("fragment_statuses")
    fragments = fragments.get("data") if isinstance(fragments, dict) else None
    if not isinstance(fragments, list) or len(fragments) != len(expected):
        raise fail("PUBLISH_READ_INCOMPLETE", "Publication has not proved every selected fragment", True)
    if any(not isinstance(item, dict) for item in fragments):
        raise fail("PUBLISH_READ_INCOMPLETE", "Publication fragment result is malformed", True)
    ids = [item.get("fragment_id") for item in fragments]
    if any(not isinstance(value, str) for value in ids):
        raise fail("PUBLISH_READ_INCOMPLETE", "Publication fragment identity is malformed", True)
    if len(set(ids)) != len(ids) or set(ids) != expected:
        raise fail("PUBLISH_SCOPE_MISMATCH", "Publication returned another fragment set")
    if any(item.get("status") != "SUCCESS" for item in fragments):
        raise fail("PUBLISH_FRAGMENT_FAILED", "One or more advertising fragments did not succeed")
    for name, expected_count in (("total_count", len(expected)), ("success_count", len(expected)),
                                 ("error_count", 0), ("canceled_count", 0),
                                 ("in_progress_count", 0), ("initial_count", 0)):
        count = row.get(name)
        if isinstance(count, bool) or not isinstance(count, int) or count != expected_count:
            raise fail("PUBLISH_READ_INCOMPLETE", "Publication counters do not prove completion", True)
    return {"completed": True, "status": status, "operation_id": operation_id,
            "fragment_ids": sorted(expected), "objects_verified": False}
