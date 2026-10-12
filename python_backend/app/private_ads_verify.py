"""Independent published-object reads for the private Ads Manager publisher."""
from __future__ import annotations

import copy

from .private_ads_draft import fail, identity


def _same(actual, expected):
    if isinstance(expected, int) and not isinstance(expected, bool):
        return actual == expected if isinstance(actual, int) and not isinstance(actual, bool) else (
            isinstance(actual, str) and actual == str(expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _same(actual[key], value)
                                               for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


async def verify_published_objects(client, objects, levels):
    account = client.context.account_id
    entities = {"campaign_id": identity(objects["campaign"]),
                "adset_id": identity(objects["ad_set"]), "ad_id": identity(objects["ad"])}
    specifications = copy.deepcopy(levels)
    specifications["ad_set"]["campaign_id"] = entities["campaign_id"]
    specifications["ad"].update({"campaign_id": entities["campaign_id"], "adset_id": entities["adset_id"]})
    creative = specifications["ad"].pop("creative", None)
    if not isinstance(creative, dict) or not isinstance(creative.get("object_story_spec"), dict):
        raise fail("CREATIVE_SPEC_UNVERIFIED", "Published creative requires its expected Page and media specification")
    for level, entity_name in (("campaign", "campaign_id"), ("ad_set", "adset_id"), ("ad", "ad_id")):
        expected = specifications[level]
        fields = sorted({"id", "account_id", "status", *expected,
                         *({"creative{id}"} if level == "ad" else set())})
        row = await client.get("/" + entities[entity_name], {"fields": fields})
        _scope(row, account, entities[entity_name])
        if row.get("status") != "PAUSED" or any(key not in row or not _same(row[key], value)
                                               for key, value in expected.items()):
            raise fail("OBJECT_SPEC_MISMATCH", "Published advertising settings differ from the saved Launch")
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
            "source": "independent_private_ads_http", "browser_started": False}


def _scope(row, account, entity):
    if not isinstance(row, dict) or row.get("error") or row.get("errors") or row.get("id") != entity:
        raise fail("OBJECT_ID_MISMATCH", "Independent read did not return the saved advertising object")
    returned = row.get("account_id")
    if not isinstance(returned, str) or returned.removeprefix("act_") != account:
        raise fail("OBJECT_SCOPE_MISMATCH", "Independent read returned another advertising account")
