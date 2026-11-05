"""Narrow, read-only recovery for saved paused draft fragments.

Try the exact fragment node, owning draft edge and (for Campaigns) the saved
numeric ID as an object before reviewed account-level reads.
"""
from __future__ import annotations

import logging

from .private_ads_draft import FRAGMENT_FIELDS, fail, identity
from .private_ads_verify import _same


_ALLOWED_EDGES = {"campaign": "campaigns", "ad_set": "adsets", "ad": "ads"}
_CORE_FRAGMENT_FIELDS = [
    "account_id", "ad_draft_id", "ad_object_id", "ad_object_type", "id",
    "parent_ad_object_id", "status", "values", "validation_status",
]
log = logging.getLogger(__name__)


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


def _account(row, account_id, object_id=None):
    if (not isinstance(row, dict) or "error" in row or "errors" in row
            or row.get("id") != object_id and object_id is not None):
        raise fail("READ_SCOPE_MISMATCH", "Meta object read did not return the exact saved identity")
    returned_account = row.get("account_id")
    if (not isinstance(returned_account, str)
            or returned_account.removeprefix("act_") != account_id):
        raise fail("READ_SCOPE_MISMATCH", "Meta object read returned another advertising account")


def _matches(row, values, *, exclude=()):
    return all(key in row and _same(row[key], value, key)
               for key, value in values.items() if key not in exclude)


async def _read_fragment_from_draft_edge(client, *, draft_id, fragment_id):
    """Read one exact fragment through the already confirmed owning draft."""
    response = await client.get(
        "/addraft_" + identity(draft_id) + "/addraft_fragments",
        {"fields": sorted(_CORE_FRAGMENT_FIELDS), "limit": 100},
    )
    if not isinstance(response, dict) or "error" in response or "errors" in response:
        raise fail("READ_SCOPE_MISMATCH", "Meta draft fragment edge returned an invalid response")
    rows = response.get("data")
    if not isinstance(rows, list) or len(rows) > 100:
        raise fail("READ_PENDING", "Meta did not return a bounded draft fragment list")
    paging = response.get("paging")
    if isinstance(paging, dict) and paging.get("next"):
        raise fail("READ_PENDING", "Draft fragment list is paginated; uniqueness was not confirmed")
    if any(not isinstance(row, dict) or "error" in row or "errors" in row for row in rows):
        raise fail("READ_SCOPE_MISMATCH", "Meta returned an invalid draft fragment")
    matches = [row for row in rows if row.get("id") == fragment_id]
    if len(matches) > 1:
        raise fail("READ_RELATION_MISMATCH", "Meta returned duplicate identities for the saved draft fragment")
    return matches[0] if matches else None


async def _read_saved_campaign_by_fragment_id(
    client, *, account_id, draft_id, fragment_id, values, fragment_read_error,
):
    """Use the durable fragment receipt as a Campaign ID only if it reads exactly."""
    if not isinstance(values, dict) or values.get("status") != "PAUSED":
        raise fail("READ_PENDING", "Saved Campaign has no explicit PAUSED specification")
    account_id, draft_id, fragment_id = (
        identity(account_id), identity(draft_id), identity(fragment_id))
    fields = sorted({"id", "account_id", *values.keys()})
    try:
        row = await client.get("/" + fragment_id, {"fields": fields})
    except Exception as error:
        if is_exact_fragment_read_rejection(error):
            diagnostic = error.diagnostic
            log.info("saved_campaign_id_read_unavailable outcome=unsupported code=%s subcode=%s",
                diagnostic.get("code"), diagnostic.get("error_subcode"))
            return None, error
        raise
    _account(row, account_id, fragment_id)
    if "status" not in row:
        log.info("saved_campaign_id_read_unavailable outcome=projection_incomplete missing_count=%d",
            len(set(values) - set(row)))
        return None, None
    if row.get("status") != "PAUSED":
        raise fail("DRAFT_DELIVERY_UNSAFE", "Direct saved Campaign ID is not PAUSED")
    missing = set(values) - set(row)
    if missing:
        log.info("saved_campaign_id_read_unavailable outcome=projection_incomplete missing_count=%d",
            len(missing))
        return None, None
    if not _matches(row, values, exclude={"status"}):
        raise fail("READ_SPEC_MISMATCH", "Direct saved Campaign ID differs from the full saved specification")
    proof = {
        "source": "direct_saved_campaign_id_read",
        "level": "campaign",
        "object_id": fragment_id,
        "creative_id": None,
        "account_id": account_id,
        "draft_id": draft_id,
        "fragment_id": fragment_id,
        "status": "PAUSED",
        "settings_match": True,
        "unique_name_match_count": 1,
        "settings_match_count": 1,
        "unique_spec_match_count": 1,
        "paused_spec_match_count": 1,
        "pagination_complete": True,
        "validation_unobserved": True,
        "fragment_read_error": fragment_read_error,
        "checked_fields": fields,
    }
    log.info("saved_campaign_id_read_confirmed outcome=exact_paused_match")
    return proof, None


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
    if level == "ad" and (
        parent_id is None or values.get("adset_id") != parent_id
        or not isinstance(values.get("creative"), dict)
    ):
        raise fail("READ_RELATION_MISMATCH", "Saved Ad does not name the confirmed Ad Set and creative")

    list_fields = {"id", "account_id", *values.keys()}
    if level == "ad":
        list_fields.discard("creative")
        list_fields.add("creative{id}")
    fields = sorted(list_fields)
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
        _account(row, account_id)
        same_name.append(row)

    if not same_name:
        log.warning("saved_account_object_read_unmatched level=%s outcome=no_saved_name same_name_count=0", level)
        raise fail("READ_RELATION_MISMATCH", "Meta did not return an object with the saved name")
    excluded = {"status"}
    if level == "ad":
        excluded.add("creative")
    settings_matches = [
        row for row in same_name if _matches(row, values, exclude=excluded)
    ]
    paused_matches = [row for row in settings_matches if row.get("status") == "PAUSED"]
    if len(paused_matches) > 1:
        log.warning("saved_account_object_read_unmatched level=%s outcome=ambiguous_paused_spec same_name_count=%d settings_match_count=%d paused_spec_match_count=%d",
            level, len(same_name), len(settings_matches), len(paused_matches))
        raise fail("READ_RELATION_MISMATCH", "Meta returned multiple exact paused objects for the saved draft")
    if not paused_matches:
        log.warning("saved_account_object_read_unmatched level=%s outcome=no_paused_spec same_name_count=%d settings_match_count=%d paused_spec_match_count=0",
            level, len(same_name), len(settings_matches))
        if settings_matches:
            raise fail("DRAFT_DELIVERY_UNSAFE", "Exact saved settings did not have PAUSED status")
        raise fail("READ_SPEC_MISMATCH", "Meta returned no object matching the full saved Launch settings")
    row = paused_matches[0]
    object_id = identity(row.get("id"))

    creative_id = None
    checked_fields = fields
    if level == "campaign":
        source = "account_campaigns_read"
    elif level == "ad_set":
        source = "account_adsets_read"
    else:
        source = "account_ads_read"
        if row.get("campaign_id") != values.get("campaign_id") or row.get("adset_id") != parent_id:
            raise fail("READ_RELATION_MISMATCH", "Account Ad belongs to another Campaign or Ad Set")
        ad_fields = sorted({"id", "account_id", *values.keys()} - {"creative"})
        ad_fields.append("creative{id}")
        ad_row = await client.get("/" + object_id, {"fields": ad_fields})
        _account(ad_row, account_id, object_id)
        if ad_row.get("status") != "PAUSED" or not _matches(
            ad_row, values, exclude={"creative"}
        ):
            raise fail("READ_SPEC_MISMATCH", "Direct Ad read differs from the saved Launch")
        creative = ad_row.get("creative")
        if not isinstance(creative, dict):
            raise fail("READ_SPEC_MISMATCH", "Direct Ad read did not return its Creative identity")
        creative_id = identity(creative.get("id"))
        creative_values = values["creative"]
        creative_fields = sorted({"id", "account_id", *creative_values.keys()})
        creative_row = await client.get("/" + creative_id, {"fields": creative_fields})
        _account(creative_row, account_id, creative_id)
        if not _matches(creative_row, creative_values):
            raise fail("READ_SPEC_MISMATCH", "Direct Creative read differs from the saved Launch")
        checked_fields = sorted(set(fields + ad_fields + creative_fields))

    return {
        "source": source,
        "level": level,
        "object_id": object_id,
        "creative_id": creative_id,
        "account_id": account_id,
        "draft_id": draft_id,
        "fragment_id": fragment_id,
        "status": "PAUSED",
        "settings_match": True,
        "unique_name_match_count": len(same_name),
        "settings_match_count": len(settings_matches),
        "unique_spec_match_count": len(paused_matches),
        "paused_spec_match_count": len(paused_matches),
        "pagination_complete": True,
        "validation_unobserved": True,
        "fragment_read_error": fragment_read_error or {},
        "checked_fields": checked_fields,
    }


async def read_fragment_with_account_object_fallback(
    client, *, account_id, draft_id, fragment_id, level, values, parent_id=None,
    allow_fallback=False,
):
    fragment_path = "/addraftfragment_" + identity(fragment_id)
    try:
        row = await client.get(fragment_path, {"fields": FRAGMENT_FIELDS})
        return {"row": row, "fallback": None, "source": "fragment_node"}
    except Exception as error:
        if (level not in _ALLOWED_EDGES
                or not is_exact_fragment_read_rejection(error)):
            raise
        # Meta may reject optional fields on an otherwise readable draft
        # fragment. Retry the same node with only the fields required to verify
        # ownership, hierarchy, settings and paused validation.
        try:
            row = await client.get(fragment_path, {"fields": _CORE_FRAGMENT_FIELDS})
            return {"row": row, "fallback": None, "source": "fragment_node_core"}
        except Exception as core_error:
            if not is_exact_fragment_read_rejection(core_error):
                raise
            error = core_error
        log.info("saved_fragment_core_read_rejected level=%s code=%s subcode=%s",
            level, error.diagnostic.get("code"), error.diagnostic.get("error_subcode"))

        # The fragment was submitted through this exact draft's edge. Read that
        # edge before trying account-wide name matching, and accept only one
        # row whose ID equals the durable fragment receipt.
        try:
            row = await _read_fragment_from_draft_edge(
                client, draft_id=draft_id, fragment_id=identity(fragment_id))
        except Exception as edge_error:
            if not is_exact_fragment_read_rejection(edge_error):
                raise
            error = edge_error
            log.info("saved_fragment_edge_read_rejected level=%s code=%s subcode=%s",
                level, error.diagnostic.get("code"), error.diagnostic.get("error_subcode"))
        else:
            if row is not None:
                return {"row": row, "fallback": None, "source": "draft_edge"}
            log.info("saved_fragment_edge_read_unmatched level=%s", level)

        if not allow_fallback:
            raise error
        diagnostic = error.diagnostic
        read_error = {
            key: diagnostic[key]
            for key in ("http_status", "code", "error_subcode", "type")
            if key in diagnostic
        }
        if level == "campaign":
            direct_proof, direct_error = await _read_saved_campaign_by_fragment_id(
                client, account_id=account_id, draft_id=draft_id, fragment_id=fragment_id,
                values=values, fragment_read_error=read_error)
            if direct_proof is not None:
                return {"row": None, "fallback": direct_proof,
                    "source": "direct_saved_campaign_id_read"}
            if direct_error is not None:
                error = direct_error
        proof = await _read_account_level_object(
            client, account_id=account_id, level=level, values=values,
            parent_id=parent_id, draft_id=draft_id, fragment_id=fragment_id,
            fragment_read_error=read_error,
        )
        return {"row": None, "fallback": proof, "source": "account_object"}
