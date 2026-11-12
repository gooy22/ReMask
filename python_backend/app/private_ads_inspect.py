"""Fresh reads of a saved draft intent, without replaying any draft operation."""
from __future__ import annotations

import copy
from functools import lru_cache
import json
import logging
import re

from .private_ads_draft import LEVELS, fail, fragment_parent_matches, fragment_values_match, identity
from .private_ads_draft_fallback import (
    _read_account_level_object,
    is_exact_fragment_read_rejection,
    read_fragment_with_account_object_fallback,
)
from .private_ads_http import AdsManagerPrivateHttp
from .private_launch import _launch_fingerprint
from .provisioning.models import ProvisioningError

# Exact reviewed senders at main 2f5444324d8e and 3a225cf6dac8. Inspection only;
# it does not authorize a current sender to resume an old mutation.
READ_ONLY_DRAFT_PREDECESSORS = frozenset({
    "f9f8e21841da2cf174bc516941a5581fe1c5b65642783ab84ed2b03ede68c218",
    "9b2b414e40aa4d6a1d69dfdfefee07341a45fa18b4328a72f185e63c28d00c44",
})


_ERROR_TERMS = frozenset("objective discontinued unsupported optimization billing payment method card funding confirmation destination promoted conversion attribution targeting placement budget schedule campaign adset creative permission ownership account restricted disabled invalid required missing incompatible duplicate parent image video page pixel currency name field validation status not no without never cannot can't isn't aren't doesn't didn't wasn't weren't shouldn't wouldn't won't".split())
_ERROR_CODES = frozenset({"code", "error_code", "error_subcode", "subcode", "exception_code"})
_SECRET_KEYS = re.compile(r"cookie|token|secret|password|credential|authorization|dtsg|lsd|session", re.I)
_ERROR_MESSAGE_KEYS = frozenset({"message", "error_message", "user_message", "error_user_msg"})
_REPORTED_FIELD_KEYS = frozenset({
    "field", "field_name", "field_names", "param", "parameter", "parameter_name",
    "blame_field_specs", "blame_field_names", "blame_fields", "field_path", "field_paths",
    "invalid_field", "unsupported_field",
})
_REPORTED_FIELD_VALUE_KEYS = frozenset({
    "field", "field_name", "field_names", "param", "parameter", "parameter_name",
    "path", "paths", "fields",
})
_FIELD_PATH_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:(?:\.[A-Za-z][A-Za-z0-9_]*)|(?:\[[0-9]{1,3}\])){0,12}$"
)
_FIELD_TOKEN = r"[A-Za-z][A-Za-z0-9_]*(?:(?:\.[A-Za-z][A-Za-z0-9_]*)|(?:\[[0-9]{1,3}\])){0,12}"
_MESSAGE_FIELD_PATTERNS = (
    re.compile(
        r"\b(?:field(?:\s+(?:name|path))?|parameter|param)\b"
        r"\s*(?:[:=]|\b(?:is|was|named|called)\b)\s*[\x60'\"“”]*("
        + _FIELD_TOKEN + r")",
        re.I,
    ),
    re.compile(
        r"\b(?:invalid|unknown|unsupported|required|missing)\s+(?:field|parameter|param)\s+[\x60'\"“”]*("
        + _FIELD_TOKEN + r")",
        re.I,
    ),
    re.compile(
        r"\b(?:field(?:\s+(?:name|path))?|parameter|param)\s+[\x60'\"“”]+("
        + _FIELD_TOKEN + r")[\x60'\"“”]+",
        re.I,
    ),
)
_MAX_REPORTED_FIELD_PATHS_PER_RECORD = 16
_MAX_DIAGNOSTIC_RECORDS = 8
_MAX_DIAGNOSTIC_TERMS_PER_RECORD = 24
_SAFE_RESPONSE_KEY_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MAX_DIAGNOSTIC_RESPONSE_KEYS = 64
_MAX_DIAGNOSTIC_TEXT_FIELDS = 16
_MAX_DIAGNOSTIC_SHAPE_NODES = 256


@lru_cache(maxsize=1)
def _validation_fields():
    from .private_ads_catalog import AdsManagerRouteCatalog
    fields = {key for values in AdsManagerRouteCatalog().data["object_fields"].values() for key in values}
    return frozenset(fields | {"object_story_spec", "link_data", "page_id", "image_hash", "video_id"})


def validation_diagnostic(value, *, observed):
    """Bounded typed evidence, never raw Meta messages or credential-bearing data.

    Error strings can echo requests. Keep numeric Meta codes, reviewed field
    names and an allowlisted vocabulary; do not persist arbitrary strings or URLs.
    Preserve safe term order and error-record association so separate Meta errors
    and negations are not collapsed into one unordered global vocabulary.
    """
    fields = _validation_fields()
    terms, names, codes = set(), set(), set()
    find_visited, walk_visited, truncated = 0, 0, False

    def parse_encoded(node):
        if not isinstance(node, str) or len(node) > 16000 or not node.lstrip().startswith(("{", "[")):
            return None
        try:
            decoded = json.loads(node)
        except (ValueError, RecursionError):
            return None
        return decoded if isinstance(decoded, (dict, list)) else None

    record_nodes = []

    def find_records(node, key="", depth=0):
        nonlocal find_visited, truncated
        find_visited += 1
        if find_visited > 256 or depth > 8:
            truncated = True
            return
        if _SECRET_KEYS.search(key):
            return
        if isinstance(node, dict):
            if any(child_key in _ERROR_CODES or child_key in _ERROR_MESSAGE_KEYS
                   for child_key in node if isinstance(child_key, str)):
                if len(record_nodes) < _MAX_DIAGNOSTIC_RECORDS:
                    record_nodes.append(node)
                else:
                    truncated = True
                return
            if len(node) > 64:
                truncated = True
            for child_key, child in list(node.items())[:64]:
                if isinstance(child_key, str):
                    find_records(child, child_key, depth + 1)
        elif isinstance(node, list):
            if len(node) > 64:
                truncated = True
            for child in node[:64]:
                before = len(record_nodes)
                find_records(child, key, depth + 1)
                if len(record_nodes) == before and child is not None:
                    if len(record_nodes) < _MAX_DIAGNOSTIC_RECORDS:
                        record_nodes.append(child)
                    else:
                        truncated = True
        elif isinstance(node, str):
            if len(node) > 16000:
                truncated = True
            decoded = parse_encoded(node)
            if decoded is not None:
                find_records(decoded, key, depth + 1)

    find_records(value)
    if not record_nodes and value not in (None, [], {}):
        # Some supported responses expose a compact message or field without
        # an error wrapper. Keep its safe evidence as one bounded record.
        record_nodes.append(value)

    diagnostic_records = []
    reported_fields = set()

    def collect_reported_field_paths(node, record, depth=0):
        nonlocal truncated
        if depth > 8:
            record["truncated"] = True
            truncated = True
            return
        if isinstance(node, str):
            candidate = node.strip()
            if len(candidate) <= 256 and _FIELD_PATH_PATTERN.fullmatch(candidate):
                paths = record["reported_field_paths"]
                if candidate not in paths and len(paths) >= _MAX_REPORTED_FIELD_PATHS_PER_RECORD:
                    record["truncated"] = True
                    truncated = True
                else:
                    paths.add(candidate)
        elif isinstance(node, list):
            if len(node) > 64:
                record["truncated"] = True
                truncated = True
            for child in node[:64]:
                collect_reported_field_paths(child, record, depth + 1)
        elif isinstance(node, dict):
            if len(node) > 64:
                record["truncated"] = True
                truncated = True
            for child_key, child in list(node.items())[:64]:
                if isinstance(child_key, str) and child_key.casefold() in _REPORTED_FIELD_VALUE_KEYS:
                    collect_reported_field_paths(child, record, depth + 1)

    def safe_record_shape(node):
        """Keep bounded response-key paths and allowlisted words, never raw values."""
        response_keys = set()
        text_evidence = []
        visited = 0
        shape_truncated = False

        def add_key_path(path):
            nonlocal shape_truncated
            if not path:
                return
            if len(path) > 180:
                shape_truncated = True
                return
            if path not in response_keys and len(response_keys) >= _MAX_DIAGNOSTIC_RESPONSE_KEYS:
                shape_truncated = True
                return
            response_keys.add(path)

        def visit(value, path="", depth=0):
            nonlocal visited, shape_truncated
            visited += 1
            if visited > _MAX_DIAGNOSTIC_SHAPE_NODES or depth > 8:
                shape_truncated = True
                return
            if isinstance(value, dict):
                if len(value) > 64:
                    shape_truncated = True
                for raw_key, child in list(value.items())[:64]:
                    if not isinstance(raw_key, str):
                        shape_truncated = True
                        continue
                    if _SECRET_KEYS.search(raw_key):
                        continue
                    if not _SAFE_RESPONSE_KEY_SEGMENT.fullmatch(raw_key):
                        shape_truncated = True
                        visit(child, path, depth + 1)
                        continue
                    child_path = f"{path}.{raw_key}" if path else raw_key
                    add_key_path(child_path)
                    visit(child, child_path, depth + 1)
            elif isinstance(value, list):
                if len(value) > 64:
                    shape_truncated = True
                child_path = path + "[]" if path else "value[]"
                for child in value[:64]:
                    visit(child, child_path, depth + 1)
            elif isinstance(value, str):
                if len(value) > 16000:
                    shape_truncated = True
                source = value[:16000]
                decoded = parse_encoded(source)
                if decoded is not None:
                    visit(decoded, path, depth + 1)
                    return
                matched_terms = []
                for word in re.findall(r"[a-z_]+n't|[a-z_]+", source.casefold()):
                    if word in _ERROR_TERMS and len(matched_terms) < _MAX_DIAGNOSTIC_TERMS_PER_RECORD:
                        matched_terms.append(word)
                field_paths = set()
                for pattern in _MESSAGE_FIELD_PATTERNS:
                    for match in pattern.finditer(source):
                        field_paths.add(match.group(1))
                if not matched_terms and not field_paths:
                    return
                if len(text_evidence) >= _MAX_DIAGNOSTIC_TEXT_FIELDS:
                    shape_truncated = True
                    return
                text_evidence.append({
                    "key_path": path or "value",
                    "matched_terms_in_order": matched_terms,
                    "reported_field_paths": sorted(field_paths)[:_MAX_REPORTED_FIELD_PATHS_PER_RECORD],
                })

        visit(node)
        return {
            "response_keys": sorted(response_keys),
            "text_evidence": text_evidence,
            "truncated": shape_truncated,
        }

    def walk_record(node, key, depth, record):
        nonlocal walk_visited, truncated
        walk_visited += 1
        if walk_visited > 256 or depth > 8:
            record["truncated"] = True
            truncated = True
            return
        if _SECRET_KEYS.search(key):
            return
        if isinstance(key, str) and key.casefold() in _REPORTED_FIELD_KEYS:
            collect_reported_field_paths(node, record)
        if key in fields:
            record["field_names"].add(key)
        if key in _ERROR_CODES:
            if type(node) is int and 0 <= node <= 2_147_483_647:
                record["codes"].add((key, node))
            elif isinstance(node, str) and re.fullmatch(r"[0-9]{1,10}", node) and int(node) <= 2_147_483_647:
                record["codes"].add((key, int(node)))
        if isinstance(node, dict):
            if len(node) > 64:
                record["truncated"] = True
                truncated = True
            for child_key, child in list(node.items())[:64]:
                if isinstance(child_key, str):
                    walk_record(child, child_key, depth + 1, record)
        elif isinstance(node, list):
            if len(node) > 64:
                record["truncated"] = True
                truncated = True
            for child in node[:64]:
                walk_record(child, key, depth + 1, record)
        elif isinstance(node, str):
            if len(node) > 16000:
                record["truncated"] = True
                truncated = True
            source = node[:16000]
            # Meta places human-readable validation text under keys that vary
            # by operation (for example summary/details). Scan every bounded,
            # non-secret string leaf for an explicit field/parameter phrase;
            # only the captured path is retained, never the source text.
            for pattern in _MESSAGE_FIELD_PATTERNS:
                for match in pattern.finditer(source):
                    collect_reported_field_paths(match.group(1), record)
            decoded = parse_encoded(source)
            if decoded is not None:
                walk_record(decoded, key, depth + 1, record)
                return
            for word in re.findall(r"[a-z_]+n't|[a-z_]+", source.casefold()):
                if word in _ERROR_TERMS and len(record["matched_terms_in_order"]) < _MAX_DIAGNOSTIC_TERMS_PER_RECORD:
                    record["matched_terms_in_order"].append(word)
                if word in fields:
                    record["field_names"].add(word)

    for index, record_node in enumerate(record_nodes):
        record = {"codes": set(), "field_names": set(), "reported_field_paths": set(),
                  "matched_terms_in_order": [], "truncated": False}
        walk_record(record_node, "", 0, record)
        record_codes = [{"field": field, "value": number} for field, number in sorted(record["codes"])]
        record_fields = sorted(record["field_names"])
        record_reported_fields = sorted(record["reported_field_paths"])
        record_terms = record["matched_terms_in_order"]
        shape = safe_record_shape(record_node)
        record_truncated = record["truncated"] or shape["truncated"]
        truncated = truncated or shape["truncated"]
        codes.update((entry["field"], entry["value"]) for entry in record_codes)
        names.update(record_fields)
        reported_fields.update(record_reported_fields)
        terms.update(record_terms)
        diagnostic_records.append({"index": index, "codes": record_codes,
            "field_names": record_fields, "reported_field_paths": record_reported_fields,
            "matched_terms_in_order": record_terms,
            "response_keys": shape["response_keys"], "text_evidence": shape["text_evidence"],
            "truncated": record_truncated})

    return {"observed": observed, "is_list": isinstance(value, list), "has_errors": bool(value),
        "exact_cause_confirmed": False,
        "codes": [{"field": key, "value": number} for key, number in sorted(codes)],
        "terms": sorted(terms), "field_names": sorted(names),
        "reported_field_paths": sorted(reported_fields), "records": diagnostic_records,
        "truncated": truncated}


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
        verified_object = evidence.get("verification", {}).get("ad_object_id")
        receipt_object = receipt.get("ad_object_id")
        if verified_object is not None:
            verified_object = identity(verified_object)
        if receipt_object is not None:
            receipt_object = identity(receipt_object)
        if (verified_object is not None and receipt_object is not None
                and verified_object != receipt_object):
            raise fail("SAVED_OPERATION_MISMATCH", "Saved Campaign object receipt differs from its verification")
        confirmed_object = verified_object or receipt_object
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
            "meta_validation_complete": False, "meta_validation_errors_present": False,
            "draft_verified": False,
            "relationships_confirmed": False, "editor_verified": False, "published": None,
            "pending_stage": pending, "uninspected_levels": list(LEVELS),
            "unverified_object_levels": [], "launch_fingerprint_verified": True,
            "actor_verified": True, "payment_checked_live": False, "mutation_posts": 0,
            "source": "saved_account_scoped_journal", "recovered_source_digest": source,
            "source_reviewed_for_mutation": source in {adapter.source_digest, *READ_ONLY_DRAFT_PREDECESSORS}}
    web = await session.facebook_web()
    client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
        business_id=target["business_id"], actor_id=actor)
    client.read_only = True
    account = await client.get("/act_" + target["ad_account_id"], {"fields": ["id", "account_id", "business"]})
    secondary_account = account.get("account_id") if isinstance(account, dict) else None
    if (not isinstance(account, dict) or "error" in account or "errors" in account
            or not isinstance(account.get("id"), str)
            or account["id"].removeprefix("act_") != target["ad_account_id"]
            or (secondary_account is not None and
                (not isinstance(secondary_account, str) or
                 secondary_account.removeprefix("act_") != target["ad_account_id"]))
            or not isinstance(account.get("business"), dict)
            or account["business"].get("id") != target["business_id"]):
        raise fail("READ_SCOPE_MISMATCH", "Fresh account read did not confirm the saved Business")
    draft = await client.get("/addraft_" + draft_id, {"fields": ["id", "account_id", "author_id", "ownership_type"]})
    adapter_scope(draft, target["ad_account_id"])
    if draft.get("id") != draft_id or draft.get("author_id") != actor or draft.get("ownership_type") != "USER":
        raise fail("READ_SCOPE_MISMATCH", "Fresh draft ownership differs from the saved intent")
    objects, reads, object_reads = {}, [], []
    for level, fragment_id, values, confirmed_object in fragments:
        parent = objects.get("campaign" if level == "ad_set" else "ad_set") if level != "campaign" else None
        observed = await read_fragment_with_account_object_fallback(
            client, account_id=target["ad_account_id"], draft_id=draft_id,
            fragment_id=fragment_id, level=level, values=values,
            parent_id=parent, object_id=confirmed_object, allow_fallback=True)
        fallback = observed.get("fallback")
        if fallback is not None:
            if (not isinstance(fallback, dict)
                    or fallback.get("account_id") != target["ad_account_id"]
                    or fallback.get("status") != "PAUSED"
                    or fallback.get("settings_match") is not True
                    or fallback.get("unique_spec_match_count") != 1
                    or fallback.get("paused_spec_match_count") != 1
                    or fallback.get("pagination_complete") is not True):
                raise fail("READ_RELATION_MISMATCH", "Account fallback did not prove one exact paused object")
            object_id = identity(fallback.get("object_id"))
            if confirmed_object is not None and object_id != confirmed_object:
                raise fail("READ_RELATION_MISMATCH", "Fresh account object differs from the saved fragment receipt")
            if object_id in objects.values():
                raise fail("READ_RELATION_MISMATCH", "Fresh draft levels have duplicate object identities")
            objects[level] = object_id
            reads.append({"level": level, "fragment_id": fragment_id, "ad_object_id": object_id,
                "read_source": fallback.get("source", "account_object"),
                "fragment_relation_verified": False, "fragment_storage_verified": False,
                "account_fallback_verified": True, "unique_spec_match_count": 1,
                "paused_spec_match_count": 1, "submitted_values": values, "settings_match": True,
                "validation_status": None, "active_errors_present": False,
                "active_errors_observed": False, "active_errors_confirmed_empty": False,
                "active_error_diagnostic": validation_diagnostic(None, observed=False),
                "publish_status": None, "status": "PAUSED"})
            if fallback.get("source") == "direct_saved_campaign_object_read":
                observed_values = fallback.get("object_values", {})
                object_reads.append({"level": level, "id": object_id, "available": True,
                    "values": copy.deepcopy(observed_values), "settings_match": True,
                    "missing_fields": sorted(set(values) - set(observed_values)),
                    "effective_status": None, "publication_verified": False,
                    "read_source": fallback["source"]})
            continue
        row = observed["row"]
        adapter_scope(row, target["ad_account_id"])
        if (row.get("id") != fragment_id or row.get("ad_draft_id") != draft_id or row.get("ad_object_type") != level
                or not fragment_parent_matches(row.get("parent_ad_object_id"), level=level,
                    parent_id=parent, root_account_id=getattr(client, "draft_root_parent_id", client.context.account_id))):
            raise fail("READ_RELATION_MISMATCH", "Fresh fragment relationships differ from the saved draft")
        if level != "campaign" and values.get("campaign_id") != objects["campaign"]:
            raise fail("READ_RELATION_MISMATCH", "Saved Ad Set or Ad has another Campaign relationship")
        if level == "ad" and values.get("adset_id") != objects["ad_set"]:
            raise fail("READ_RELATION_MISMATCH", "Saved Ad has another Ad Set relationship")
        if not fragment_values_match(row.get("values"), values, business_id=target["business_id"]):
            # Import only on the read-failure path; this module supplies the
            # saved-intent checks also used by the diagnostic endpoint.
            from .private_draft_read_diagnostic import values_diagnostic
            logging.getLogger(__name__).warning("draft_value_mismatch profile=%s level=%s diagnostic=%s",
                profile_id, level, json.dumps(values_diagnostic(row.get("values"), values,
                    target["business_id"]), sort_keys=True, separators=(",", ":")))
            raise fail("READ_SPEC_MISMATCH", "Fresh draft values differ from the original submitted fields")
        objects[level] = identity(row.get("ad_object_id"))
        if confirmed_object is not None and objects[level] != confirmed_object:
            raise fail("READ_RELATION_MISMATCH", "Fresh fragment refers to another previously confirmed object")
        if len(set(objects.values())) != len(objects):
            raise fail("READ_RELATION_MISMATCH", "Fresh draft levels have duplicate object identities")
        reads.append({"level": level, "fragment_id": fragment_id, "ad_object_id": objects[level],
            "read_source": observed.get("source"),
            "fragment_relation_verified": True, "fragment_storage_verified": True,
            "account_fallback_verified": False,
            "submitted_values": values, "settings_match": True,
            "fresh_values": {entry["field"]: copy.deepcopy(entry.get("new_value")) for entry in row["values"]},
            "validation_status": row.get("validation_status"), "active_errors_present": bool(row.get("active_errors")),
            "active_errors_observed": "active_errors" in row,
            "active_errors_confirmed_empty": isinstance(row.get("active_errors"), list)
                and not row["active_errors"],
            "active_error_diagnostic": validation_diagnostic(row.get("active_errors"), observed="active_errors" in row),
            "publish_status": row.get("publish_status"), "status": row.get("status")})
    # Draft fragments do not prove that Graph exposes a corresponding object.
    # Read exact retained IDs using the already reviewed published-object READ
    # projections. A received permission/unsupported-object rejection is recorded
    # as unavailable evidence, never interpreted as absence or publication.
    from .private_ads_http import AdsManagerResponseError
    from .private_ads_verify import _same, _scope
    directly_read = {(row["level"], row["id"]) for row in object_reads
                     if row.get("read_source") == "direct_saved_campaign_object_read"}
    for fragment in reads:
        level, entity = fragment["level"], fragment["ad_object_id"]
        if (level, entity) in directly_read:
            continue
        expected = copy.deepcopy(fragment["submitted_values"])
        expected.pop("creative", None)
        fields = sorted({"id", "account_id", "status", "effective_status", *expected,
                         *({"creative{id}"} if level == "ad" else set())})
        try:
            row = await client.get("/" + entity, {"fields": fields})
        except AdsManagerResponseError as exc:
            if exc.diagnostic.get("code") in {102, 190}:
                raise  # Do not repeat reads after an explicit session refusal.
            fallback_error = None
            proof = None
            if is_exact_fragment_read_rejection(exc):
                try:
                    proof = await _read_account_level_object(
                        client, account_id=target["ad_account_id"], level=level,
                        values=fragment["submitted_values"],
                        parent_id=objects.get("ad_set" if level == "ad" else "campaign")
                            if level != "campaign" else None,
                        draft_id=draft_id, fragment_id=fragment["fragment_id"],
                        expected_object_id=entity,
                        use_account_ad_row=level == "ad",
                    )
                except AdsManagerResponseError as fallback_exc:
                    if fallback_exc.diagnostic.get("code") in {102, 190}:
                        raise  # Do not continue after an explicit session refusal.
                    fallback_error = fallback_exc.diagnostic
                except ProvisioningError as fallback_exc:
                    fallback_error = {"code": fallback_exc.code}
            if proof is not None:
                observed_values = proof.get("object_values", {})
                object_reads.append({"level": level, "id": entity, "available": True,
                    "values": copy.deepcopy(observed_values), "settings_match": True,
                    "missing_fields": sorted((set(expected) - {"creative"}) - set(observed_values)),
                    "effective_status": None, "publication_verified": False,
                    "read_source": proof["source"], "identity_fallback_verified": True,
                    "direct_read_diagnostic": exc.diagnostic})
                if level == "ad":
                    object_reads.append({"level": "creative", "id": proof["creative_id"],
                        "available": True, "values": copy.deepcopy(proof["creative_values"] or {}),
                        "settings_match": True, "missing_fields": [],
                        "publication_verified": False, "read_source": "creative_exact_id_read",
                        "identity_fallback_verified": True})
                continue
            unavailable = {"level": level, "id": entity, "available": False,
                "diagnostic": exc.diagnostic, "publication_verified": False}
            if is_exact_fragment_read_rejection(exc):
                unavailable["identity_fallback_attempted"] = True
                if fallback_error is not None:
                    unavailable["identity_fallback_diagnostic"] = fallback_error
            object_reads.append(unavailable)
            continue
        _scope(row, target["ad_account_id"], entity)
        settings_match = all(field in row and _same(row[field], value, field)
                             for field, value in expected.items())
        if fragment.get("account_fallback_verified") and (
            row.get("status") != "PAUSED" or not settings_match
        ):
            raise fail("READ_SPEC_MISMATCH", "Direct object read changed from the exact paused saved specification")
        object_reads.append({"level": level, "id": entity, "available": True,
            "values": {field: copy.deepcopy(row[field]) for field in expected if field in row},
            "settings_match": settings_match,
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
                    "values": {field: copy.deepcopy(actual[field]) for field in spec if field in actual},
                    "settings_match": all(field in actual and _same(actual[field], value, field)
                                          for field, value in spec.items()),
                    "missing_fields": sorted(set(spec) - set(actual)), "publication_verified": False})
    # The job result persists this evidence. Original mutation receipts and
    # fingerprints remain untouched, even if Meta reports a validation error.
    complete = pending is None and len(fragments) == len(LEVELS)
    fragment_storage_verified = complete and all(
        row.get("fragment_storage_verified") is True for row in reads)
    relationships_confirmed = all(
        row.get("fragment_relation_verified") is True for row in reads)
    validation_complete = fragment_storage_verified and relationships_confirmed and all(
        row["validation_status"] == "VALIDATED"
        and row["active_errors_confirmed_empty"]
        for row in reads)
    validation_errors_present = any(
        row["validation_status"] == "HAS_ERRORS" or row["active_errors_present"]
        for row in reads)
    inspected = [row["level"] for row in reads]
    inspected_objects = inspected + (["creative"] if "ad" in inspected else [])
    return {"status": "SUCCESS" if fragment_storage_verified else "RECONCILE_REQUIRED",
        "execution_mode": "DRAFT_INSPECT", **target, "launch_key": key,
        "draft_id": draft_id, "draft_objects": objects, "fragment_reads": reads,
        "fragment_storage_verified": fragment_storage_verified,
        "relationships_confirmed": relationships_confirmed,
        "meta_validation_complete": validation_complete,
        "meta_validation_errors_present": validation_errors_present,
        "draft_verified": validation_complete,
        "pending_stage": pending, "uninspected_levels": [level for level in LEVELS if level not in inspected],
        "object_reads": object_reads,
        # A failed direct read or missing exact account-list match does not
        # establish that the native Meta object is absent.
        "unverified_object_levels": [level for level in inspected_objects
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
