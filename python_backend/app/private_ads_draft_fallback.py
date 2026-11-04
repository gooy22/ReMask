"""Narrow, read-only recovery for saved paused draft fragments.

The fallback is limited to the reviewed account-level Campaigns/Ad Sets reads.
It runs only after the exact fragment read returns Meta's known unsupported-object
response, then requires one uniquely named, fully matching PAUSED object.
"""
from __future__ import annotations

from .private_ads_draft import FRAGMENT_FIELDS, fail, identity
from .private_ads_verify import _same


_ALLOWED_EDGES = {"campaign": "campaigns", "ad_set": "adsets"}


def is_exact_fragment_read_rejection(error) -> bool:
    from .private_ads_http import AdsManagerResponseError

    if not isinstance(error, AdsManagerResponseError):
        return False
    diagnostic = error.diagnostic
    return (
        diagnostic.get("http_status") == 200
        and diagnostic.get("code") == 100
        and diagnostic.get("error_subcode") == 33
        and diagnostic.get("type") == "GraphMethodException"
    )


async def _read_account_level_object(
    client, *, account_id, level, values, parent_id=None, draft_id=None, fragment_id=None,
    fragment_read_error=None,
):
    edge = _ALLOWED_EDGES.get(level)
    account_id = identity(account_id)
    if edge is None or not isinstance(values, dict) or values.get("status") != "PAUSED":
        raise fail("READ_PENDING", "Saved draft cannot use an account-list fallback for this level")
    name = values.get("name")
    if not isinstance(name, str) or not name:
        raise fail("READ_PENDING", "Saved draft fallback has no exact object name")
    if level == "ad_set" and (
        parent_id is None or values.get("campaign_id") != parent_id
    ):
        raise fail("READ_RELATION_MISMATCH", "Saved Ad Set does not name the confirmed Campaign parent")

    fields = sorted({"id", "account_id", *values.keys()})
    response = await client.get(
        "/act_" + account_id + "/" + edge,
        {"fields": fields, "limit": 1000},
    )
    rows = response.get("data") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) > 1000:
        raise fail("READ_PENDING", "Meta did not return a bounded account object list")
    paging = response.get("paging") if isinstance(response, dict) else None
    if isinstance(paging, dict) and paging.get("next"):
        raise fail("READ_PENDING", "Account object list is paginated; uniqueness was not confirmed")

    same_name = []
    for row in rows:
        if not isinstance(row, dict):
            raise fail("READ_SCOPE_MISMATCH", "Meta returned an invalid account object")
        if row.get("name") != name:
            continue
        returned_account = row.get("account_id")
        if (not isinstance(returned_account, str)
                or returned_account.removeprefix("act_") != account_id):
            raise fail("READ_SCOPE_MISMATCH", "Account object list returned another advertising account")
        same_name.append(row)

    if len(same_name) != 1:
        raise fail("READ_RELATION_MISMATCH", "Meta did not return one unique object for the saved draft")
    row = same_name[0]
    object_id = identity(row.get("id"))
    if row.get("status") != "PAUSED":
        raise fail("DRAFT_DELIVERY_UNSAFE", "Account object list did not confirm PAUSED status")
    if any(key not in row or not _same(row[key], value, key) for key, value in values.items()):
        raise fail("READ_SPEC_MISMATCH", "Account object settings differ from the saved Launch")
    if level == "ad_set" and row.get("campaign_id") != parent_id:
        raise fail("READ_RELATION_MISMATCH", "Account object belongs to another Campaign")

    return {
        "source": "account_" + edge + "_read",
        "level": level,
        "object_id": object_id,
        "account_id": account_id,
        "draft_id": draft_id,
        "fragment_id": fragment_id,
        "status": "PAUSED",
        "settings_match": True,
        "unique_name_match_count": 1,
        "pagination_complete": True,
        "validation_unobserved": True,
        "fragment_read_error": fragment_read_error or {},
        "checked_fields": fields,
    }


async def read_fragment_with_account_object_fallback(
    client, *, account_id, draft_id, fragment_id, level, values, parent_id=None,
    allow_fallback=False,
):
    try:
        row = await client.get(
            "/addraftfragment_" + identity(fragment_id),
            {"fields": FRAGMENT_FIELDS},
        )
        return {"row": row, "fallback": None}
    except Exception as error:
        if (not allow_fallback or level not in _ALLOWED_EDGES
                or not is_exact_fragment_read_rejection(error)):
            raise
        diagnostic = error.diagnostic
        read_error = {
            key: diagnostic[key]
            for key in ("http_status", "code", "error_subcode", "type")
            if key in diagnostic
        }
        proof = await _read_account_level_object(
            client, account_id=account_id, level=level, values=values,
            parent_id=parent_id, draft_id=draft_id, fragment_id=fragment_id,
            fragment_read_error=read_error,
        )
        return {"row": None, "fallback": proof}
