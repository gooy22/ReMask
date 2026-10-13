"""Independent published-object reads for the private Ads Manager publisher."""
from __future__ import annotations

import copy
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from .private_ads_draft import fail, identity


def stable_media_url(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and (parsed.hostname or "").endswith(".fbcdn.net")
                and not parsed.username and not parsed.password and parsed.port is None):
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except ValueError:
        pass
    return value


def stable_request_values(value, key=None):
    # Only generated media URL fields. Destinations/UTM/text remain exact.
    if isinstance(value, dict):
        return {field: stable_request_values(child, field) for field, child in value.items()}
    if isinstance(value, list):
        return [stable_request_values(child) for child in value]
    return stable_media_url(value) if key in {"image_url", "picture"} else value


def _same(actual, expected, field=None):
    if field in {'start_time', 'end_time'} and isinstance(expected, str):
        if not isinstance(actual, str):
            return False
        try:
            left = datetime.fromisoformat(actual.replace('Z', '+00:00'))
            right = datetime.fromisoformat(expected.replace('Z', '+00:00'))
            return left.tzinfo is not None and right.tzinfo is not None and left == right
        except ValueError:
            return False
    if field in {"image_url", "picture"} and isinstance(expected, str):
        return isinstance(actual, str) and stable_media_url(actual) == stable_media_url(expected)
    if isinstance(expected, int) and not isinstance(expected, bool):
        return actual == expected if isinstance(actual, int) and not isinstance(actual, bool) else (
            isinstance(actual, str) and actual == str(expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _same(actual[key], value, key)
                                               for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


async def verify_published_objects(client, objects, levels):
    account = client.context.account_id
    entities = {"campaign_id": identity(objects["campaign"]),
                "adset_id": identity(objects["ad_set"]), "ad_id": identity(objects["ad"])}
    effective = {}
    specifications = copy.deepcopy(levels)
    specifications["ad_set"]["campaign_id"] = entities["campaign_id"]
    specifications["ad"].update({"campaign_id": entities["campaign_id"], "adset_id": entities["adset_id"]})
    creative = specifications["ad"].pop("creative", None)
    if (not isinstance(creative, dict) or not (isinstance(creative.get("object_story_spec"), dict)
            or (isinstance(creative.get("object_id"), str) and isinstance(creative.get("source_instagram_media_id"), str)))):
        raise fail("CREATIVE_SPEC_UNVERIFIED", "Published creative requires its expected Page and media specification")
    for level, entity_name in (("campaign", "campaign_id"), ("ad_set", "adset_id"), ("ad", "ad_id")):
        expected = specifications[level]
        fields = sorted({"id", "account_id", "status", "effective_status", *expected,
                         *({"creative{id}"} if level == "ad" else set())})
        row = await client.get("/" + entities[entity_name], {"fields": fields})
        _scope(row, account, entities[entity_name])
        if row.get("status") != "PAUSED" or any(key not in row or not _same(row[key], value, key)
                                               for key, value in expected.items()):
            raise fail("OBJECT_SPEC_MISMATCH", "Published advertising settings differ from the saved Launch")
        effective[level] = row.get("effective_status")
        if level == "ad":
            returned = row.get("creative")
            if not isinstance(returned, dict):
                raise fail("CREATIVE_UNVERIFIED", "Published ad did not return its creative identity", True)
            entities["creative_id"] = identity(returned.get("id"))
    row = await client.get("/" + entities["creative_id"],
        {"fields": sorted({"id", "account_id", *creative})})
    _scope(row, account, entities["creative_id"])
    if any(key not in row or not _same(row[key], value) for key, value in creative.items()):
        raise fail("CREATIVE_SPEC_MISMATCH", "Published creative does not confirm the selected Page, media or content")
    if len(set(entities.values())) != 4:
        raise fail("OBJECT_ID_MISMATCH", "Published advertising hierarchy has duplicate identities")
    return {"verified": True, "entities": entities, "account_id": account,
            "effective_statuses": effective, "delivery_running_verified": False,
            "source": "independent_private_ads_http", "browser_started": False}


def _scope(row, account, entity):
    if not isinstance(row, dict) or row.get("error") or row.get("errors") or row.get("id") != entity:
        raise fail("OBJECT_ID_MISMATCH", "Independent read did not return the saved advertising object")
    returned = row.get("account_id")
    if not isinstance(returned, str) or returned.removeprefix("act_") != account:
        raise fail("OBJECT_SCOPE_MISMATCH", "Independent read returned another advertising account")
