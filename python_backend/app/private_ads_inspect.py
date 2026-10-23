"""Fresh reads of a saved draft intent, without replaying any draft operation."""
from __future__ import annotations

import copy
from functools import lru_cache
import json
import re

from .private_ads_draft import FRAGMENT_FIELDS, LEVELS, fail, fragment_values_match, identity
from .private_ads_http import AdsManagerPrivateHttp
from .private_launch import _launch_fingerprint

# Exact reviewed senders at main 2f5444324d8e and 3a225cf6dac8. Inspection only;
# it does not authorize a current sender to resume an old mutation.
READ_ONLY_DRAFT_PREDECESSORS = frozenset({
    "f9f8e21841da2cf174bc516941a5581fe1c5b65642783ab84ed2b03ede68c218",
    "9b2b414e40aa4d6a1d69dfdfefee07341a45fa18b4328a72f185e63c28d00c44",
})


_ERROR_TERMS = frozenset("objective discontinued unsupported optimization billing payment method card funding confirmation destination promoted conversion attribution targeting placement budget schedule campaign adset creative permission ownership account restricted disabled invalid required missing incompatible duplicate parent image video page pixel currency name field validation status".split())
_ERROR_CODES = frozenset({"code", "error_code", "error_subcode", "subcode", "exception_code"})
_SECRET_KEYS = re.compile(r"cookie|token|secret|password|credential|authorization|dtsg|lsd|session", re.I)


@lru_cache(maxsize=1)
def _validation_fields():
    from .private_ads_catalog import AdsManagerRouteCatalog
    fields = {key for values in AdsManagerRouteCatalog().data["object_fields"].values() for key in values}
    return frozenset(fields | {"object_story_spec", "link_data", "page_id", "image_hash", "video_id"})


def validation_diagnostic(value, *, observed):
    """Bounded typed evidence, never raw Meta messages or credential-bearing data.

    Error strings can echo requests. Keep numeric Meta codes, reviewed field
    names and an allowlisted vocabulary; do not persist arbitrary strings or URLs.
    Parse an encoded error collection only within the same bounded budget.
    """
    fields = _validation_fields()
    terms, names, codes = set(), set(), set()
    visited, truncated = 0, False

    def walk(node, key="", depth=0):
        nonlocal visited, truncated
        visited += 1
        if visited > 256 or depth > 8:
            truncated = True
            return
        if _SECRET_KEYS.search(key):
            return
        if key in fields:
            names.add(key)
        if key in _ERROR_CODES:
            if type(node) is int and 0 <= node <= 2_147_483_647:
                codes.add((key, node))
            elif isinstance(node, str) and re.fullmatch(r"[0-9]{1,10}", node) and int(node) <= 2_147_483_647:
                codes.add((key, int(node)))
        if isinstance(node, dict):
            if len(node) > 64:
                truncated = True
            for child_key, child in list(node.items())[:64]:
                if isinstance(child_key, str):
                    walk(child, child_key, depth + 1)
        elif isinstance(node, list):
            if len(node) > 64:
                truncated = True
            for child in node[:64]:
                walk(child, key, depth + 1)
        elif isinstance(node, str):
            if len(node) > 16000:
                truncated = True
            text = node[:16000]
            words = set(re.findall(r"[a-z_]+", text.casefold()))
            terms.update(words & _ERROR_TERMS)
            names.update(words & fields)
            if text.lstrip().startswith(("{", "[")) and len(node) <= 16000:
                try:
                    decoded = json.loads(text)
                except (ValueError, RecursionError):
                    return
                if isinstance(decoded, (dict, list)):
                    walk(decoded, key, depth + 1)

    walk(value)
    return {"observed": observed, "has_errors": bool(value),
        "codes": [{"field": key, "value": number} for key, number in sorted(codes)],
        "terms": sorted(terms), "field_names": sorted(names), "truncated": truncated}


async def saved_draft_intent(adapter, *, profile_id, context, payload):
    """Verify the original immutable journal before any saved-draft operation."""
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
    if "ADS_MANAGER:DRAFT" not in saved or "ADS_MANAGER:PUBLISH" in saved:
        raise fail("RECONCILE_REQUIRED", "Inspection requires a saved unpublished draft intent; no POST was sent")
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
    if len(sources) != 1:
        raise fail("SAVED_OPERATION_MISMATCH", "Saved draft journal mixes source revisions")
    # Any single source revision may be INSPECTED, but never resumed for
    # mutation from this path. Each read rechecks the actor/account/parents/
    # exact saved fields; only the mutation sender enforces reviewed digests.

    # Inspect a contiguous saved prefix, not only the completed hierarchy.
    # A lost response has no safe entity handle: report it explicitly instead
    # of guessing an ID, creating a second draft or hiding earlier progress.
    operations = [("DRAFT", "draft_id")] + [
        ("FRAGMENT_" + level.upper(), "fragment_id") for level in LEVELS
    ]
    fragments = []
    draft_id, pending = None, None
    for index, (operation, handle) in enumerate(operations):
        name = "ADS_MANAGER:" + operation
        row = saved.get(name)
        if row is None:
            pending = {"stage": operation, "reason": "NOT_SUBMITTED"}
        else:
            receipt = row.get("result", {}).get("evidence", {}).get("receipt", {})
            if not isinstance(receipt, dict) or not receipt.get(handle):
                pending = {"stage": operation, "reason": "UNKNOWN_REMOTE_RESULT"}
        if pending:
            if any("ADS_MANAGER:" + later in saved for later, _ in operations[index + 1:]):
                raise fail("SAVED_OPERATION_MISMATCH", "Draft journal contains an impossible skipped stage")
            break
        identifier = identity(receipt[handle])
        if operation == "DRAFT":
            draft_id = identifier
            continue
        level = LEVELS[index - 1]
        evidence = row.get("result", {}).get("evidence", {})
        values = evidence.get("binding", {}).get("values")
        if not isinstance(values, dict) or not values:
            raise fail("RECONCILE_REQUIRED", "A saved fragment has no exact submitted field binding")
        confirmed_object = evidence.get("verification", {}).get("ad_object_id")
        if confirmed_object is not None:
            identity(confirmed_object)
        fragments.append((level, identifier, copy.deepcopy(values), confirmed_object))
    return target, key, fingerprint, actor, next(iter(sources)), draft_id, fragments, pending


async def inspect_saved_draft(adapter, *, profile_id, context, session, payload):
    if payload.get("reconcile_only") is not True or "repair_objective" in payload:
        raise fail("INVALID_INPUT", "Saved draft inspection must be explicitly read-only")
    target, key, fingerprint, actor, source, draft_id, fragments, pending = await saved_draft_intent(
        adapter, profile_id=profile_id, context=context, payload=payload)
    if draft_id is None:
        # There is no exact READ handle, so inspection must not even open an
        # advertising session. The journal's uncertainty is the result.
        return {"status": "RECONCILE_REQUIRED", "execution_mode": "DRAFT_INSPECT",
            **target, "launch_key": key, "draft_id": None, "draft_objects": {},
            "fragment_reads": [], "object_reads": [], "fragment_storage_verified": False,
            "relationships_confirmed": False, "editor_verified": False, "published": None,
            "pending_stage": pending, "uninspected_levels": list(LEVELS),
            "missing_direct_objects": [], "launch_fingerprint_verified": True,
            "actor_verified": True, "payment_checked_live": False, "mutation_posts": 0,
            "source": "saved_account_scoped_journal", "recovered_source_digest": source,
            "source_reviewed_for_mutation": source in {adapter.source_digest, *READ_ONLY_DRAFT_PREDECESSORS}}
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
            "active_errors_observed": "active_errors" in row,
            "active_error_diagnostic": validation_diagnostic(row.get("active_errors"), observed="active_errors" in row),
            "publish_status": row.get("publish_status"), "status": row.get("status")})
    # Draft fragments do not prove that Graph exposes a corresponding object.
    # Read exact retained IDs using the already reviewed published-object READ
    # projections. A received permission/unsupported-object rejection is recorded
    # as unavailable evidence, never interpreted as absence or publication.
    from .private_ads_http import AdsManagerResponseError
    from .private_ads_verify import _same, _scope
    object_reads = []
    for fragment in reads:
        level, entity = fragment["level"], fragment["ad_object_id"]
        expected = copy.deepcopy(fragment["submitted_values"])
        expected.pop("creative", None)
        fields = sorted({"id", "account_id", "status", "effective_status", *expected,
                         *({"creative{id}"} if level == "ad" else set())})
        try:
            row = await client.get("/" + entity, {"fields": fields})
        except AdsManagerResponseError as exc:
            if exc.diagnostic.get("code") in {102, 190}:
                raise  # Do not repeat reads after an explicit session refusal.
            object_reads.append({"level": level, "id": entity, "available": False,
                "diagnostic": exc.diagnostic, "publication_verified": False})
            continue
        _scope(row, target["ad_account_id"], entity)
        object_reads.append({"level": level, "id": entity, "available": True,
            "values": {field: copy.deepcopy(row[field]) for field in expected if field in row},
            "settings_match": all(field in row and _same(row[field], value, field)
                                  for field, value in expected.items()),
            "missing_fields": sorted(set(expected) - set(row)),
            "effective_status": row.get("effective_status"), "publication_verified": False})
        if level == "ad":
            creative = row.get("creative")
            if isinstance(creative, dict) and isinstance(creative.get("id"), str):
                creative_id = identity(creative["id"])
                spec = fragment["submitted_values"]["creative"]
                try:
                    actual = await client.get("/" + creative_id, {"fields": sorted({"id", "account_id", *spec})})
                except AdsManagerResponseError as exc:
                    if exc.diagnostic.get("code") in {102, 190}:
                        raise
                    object_reads.append({"level": "creative", "id": creative_id, "available": False,
                        "diagnostic": exc.diagnostic, "publication_verified": False})
                    continue
                _scope(actual, target["ad_account_id"], creative_id)
                object_reads.append({"level": "creative", "id": creative_id, "available": True,
                    "settings_match": all(field in actual and _same(actual[field], value, field)
                                          for field, value in spec.items()),
                    "missing_fields": sorted(set(spec) - set(actual)), "publication_verified": False})
    # The job result persists this evidence. Original mutation receipts and
    # fingerprints remain untouched, even if Meta reports a validation error.
    complete = pending is None and len(fragments) == len(LEVELS)
    inspected = [row["level"] for row in reads]
    inspected_objects = inspected + (["creative"] if "ad" in inspected else [])
    return {"status": "SUCCESS" if complete else "RECONCILE_REQUIRED",
        "execution_mode": "DRAFT_INSPECT", **target, "launch_key": key,
        "draft_id": draft_id, "draft_objects": objects, "fragment_reads": reads,
        "fragment_storage_verified": complete, "relationships_confirmed": True,
        "pending_stage": pending, "uninspected_levels": [level for level in LEVELS if level not in inspected],
        "object_reads": object_reads,
        "missing_direct_objects": [level for level in inspected_objects
            if not any(row["level"] == level and row["available"] for row in object_reads)],
        "launch_fingerprint_verified": True, "actor_verified": True,
        "editor_verified": False, "published": None, "payment_checked_live": False,
        "mutation_posts": 0, "source": "independent_private_ads_http",
        "recovered_source_digest": source,
        "source_reviewed_for_mutation": source in {adapter.source_digest, *READ_ONLY_DRAFT_PREDECESSORS}}


def adapter_scope(row, account):
    if (not isinstance(row, dict) or "error" in row or "errors" in row
            or not isinstance(row.get("account_id"), str) or row["account_id"].removeprefix("act_") != account):
        raise fail("READ_SCOPE_MISMATCH", "Fresh draft read returned another advertising account")
