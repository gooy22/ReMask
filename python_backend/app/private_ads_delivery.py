"""Change only the verified objects owned by a saved private Launch job."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json

from .private_ads_draft import (FRAGMENT_FIELDS, PUBLISH_FIELDS, fail, fragment_params,
                                fragment_values_match, identity, publication_receipt, publication_status, publish_params)
from .private_ads_http import AdsManagerPrivateHttp
from .private_ads_publication import AdsManagerDraftPublisher
from .private_ads_state import AdsManagerSubmitter
from .private_ads_verify import _same
from .private_launch_extensions import OperationKey


async def _chain(client, entities, page_id, bindings=None):
    account = client.context.account_id
    rows = {}
    for section in ("campaign", "adset", "ad", "creative"):
        fields = ["id", "account_id"]
        if section != "creative":
            fields += ["status", "effective_status"]
        if section in {"adset", "ad"}:
            fields.append("campaign_id")
        if section == "ad":
            fields += ["adset_id", "creative{id}"]
        if section == "creative":
            fields += ["object_id", "object_story_spec"]
        expected = (bindings or {}).get(section, {})
        fields = sorted(set(fields) | (set(expected) - {"creative"}))
        row = await client.get("/" + entities[section + "_id"], {"fields": fields})
        if (row.get("id") != entities[section + "_id"] or not isinstance(row.get("account_id"), str)
                or row["account_id"].removeprefix("act_") != account):
            raise fail("DELIVERY_SCOPE_MISMATCH", "Saved advertising object is not confirmed on this account")
        if section != "creative" and row.get("status") not in {"ACTIVE", "PAUSED"}:
            raise fail("DELIVERY_STATE_UNCONFIRMED", "Saved object has an unavailable delivery state")
        if section in {"adset", "ad"} and row.get("campaign_id") != entities["campaign_id"]:
            raise fail("DELIVERY_PARENT_MISMATCH", "Saved object belongs to another campaign")
        if section == "ad" and (row.get("adset_id") != entities["adset_id"]
                or not isinstance(row.get("creative"), dict) or row["creative"].get("id") != entities["creative_id"]):
            raise fail("DELIVERY_PARENT_MISMATCH", "Saved ad belongs to another AdSet or Creative")
        if section == "creative":
            story = row.get("object_story_spec")
            if not ((isinstance(story, dict) and story.get("page_id") == page_id) or row.get("object_id") == page_id):
                raise fail("DELIVERY_PAGE_MISMATCH", "Saved creative does not confirm the selected Page")
        if any(field not in row or not _same(row[field], value, field)
               for field, value in expected.items() if field not in {"status", "creative"}):
            raise fail("DELIVERY_SPEC_MISMATCH", "Owned advertising settings changed since the verified source job")
        rows[section] = row
    return rows


async def run_delivery(launch, *, profile_id, context, session, payload, task_idempotency_key=None):
    payload = copy.deepcopy(payload)
    status = payload.get("status")
    source_key = str(payload.get("source_launch_key") or "").strip()
    key = str(payload.get("delivery_key") or task_idempotency_key or "").strip()
    if status not in {"ACTIVE", "PAUSED"} or not source_key or not key or max(len(key), len(source_key)) > 200 or key == source_key:
        raise fail("INVALID_INPUT", "Delivery needs separate source and request keys and a known status")
    target = {"profile_id": str(profile_id), **{name: identity(payload.get(name))
              for name in ("business_id", "ad_account_id", "page_id")}}
    desired = payload.get("delivery") or {section: status for section in ("campaign", "adset", "ad")}
    if (not isinstance(desired, dict) or set(desired) != {"campaign", "adset", "ad"}
            or any(value not in {"ACTIVE", "PAUSED"} for value in desired.values())):
        raise fail("INVALID_INPUT", "Delivery requires explicit states for every owned object")
    actor = identity(str(context.cookies.get("c_user") or ""))
    state = launch.owner.state
    await state.init()
    source = await state.step(source_key, OperationKey("ADS_MANAGER:PUBLISH"))
    draft = await state.step(source_key, OperationKey("ADS_MANAGER:DRAFT"))
    for record in (source, draft):
        evidence = (record or {}).get("result", {}).get("evidence", {})
        if (not record or record.get("status") != "SUCCESS" or evidence.get("actor_id") != actor
                or evidence.get("source_digest") != launch.source_digest
                or evidence.get("verification", {}).get("verified") is not True
                or any(str(record.get(name)) != str(target[name]) for name in ("profile_id", "business_id", "ad_account_id"))):
            raise fail("DELIVERY_SOURCE_UNVERIFIED", "Delivery source is not a verified publication for this actor and target")
    evidence = source["result"]["evidence"]
    entities = evidence["verification"].get("entities")
    required = {"campaign_id", "adset_id", "creative_id", "ad_id"}
    if not isinstance(entities, dict) or set(entities) != required:
        raise fail("DELIVERY_SOURCE_UNVERIFIED", "Source has no complete verified object hierarchy")
    entities = {name: identity(value) for name, value in entities.items()}
    if len(set(entities.values())) != 4:
        raise fail("DELIVERY_SOURCE_UNVERIFIED", "Source object identities are ambiguous")
    draft_id = identity(draft["result"]["evidence"]["receipt"].get("draft_id"))
    bindings = {}
    for section, operation in (("campaign", "FRAGMENT_CAMPAIGN"), ("adset", "FRAGMENT_AD_SET"), ("ad", "FRAGMENT_AD")):
        record = await state.step(source_key, OperationKey("ADS_MANAGER:" + operation))
        facts = (record or {}).get("result", {}).get("evidence", {})
        values = facts.get("binding", {}).get("values")
        if (not record or record.get("status") != "SUCCESS" or facts.get("actor_id") != actor
                or facts.get("source_digest") != launch.source_digest
                or facts.get("launch_fingerprint") != evidence.get("launch_fingerprint")
                or facts.get("verification", {}).get("ad_object_id") != entities[section + "_id"]
                or not isinstance(values, dict) or not values):
            raise fail("DELIVERY_SOURCE_UNVERIFIED", "Source has no verified settings binding for every owned level")
        bindings[section] = values
    bindings["creative"] = bindings["ad"].get("creative", {})
    if not isinstance(bindings["creative"], dict) or not bindings["creative"]:
        raise fail("DELIVERY_SOURCE_UNVERIFIED", "Source has no verified creative settings")
    fingerprint = hashlib.sha256(json.dumps({"source": source_key, "target": target,
        "entities": entities, "delivery": desired}, sort_keys=True).encode()).hexdigest()
    await state.bind_plan(key, target, fingerprint, hashlib.sha256((launch.source_digest + actor).encode()).hexdigest())
    for prior in await state.all_steps(key):
        prior_evidence = prior.get("result", {}).get("evidence", {})
        if (not prior["step"].startswith("ADS_MANAGER:DELIVERY_")
                or any(str(prior.get(name)) != str(target[name]) for name in ("profile_id", "business_id", "ad_account_id"))
                or prior_evidence.get("actor_id") != actor or prior_evidence.get("source_digest") != launch.source_digest
                or prior_evidence.get("launch_fingerprint") != fingerprint):
            raise fail("DELIVERY_SOURCE_UNVERIFIED", "Delivery key belongs to another target or request")
        if not prior_evidence.get("receipt"):
            raise fail("RECONCILE_REQUIRED", "A saved delivery reply is unknown; automatic replay is blocked")
    web = await session.facebook_web()
    client = await AdsManagerPrivateHttp.open(web, account_id=target["ad_account_id"],
                                            business_id=target["business_id"], actor_id=actor)
    if launch.owner._inventory_row(context, target["business_id"], target["ad_account_id"]) is None:
        raise fail("ACCOUNT_CONTEXT_UNAVAILABLE", "Delivery account is absent from profile inventory")
    if "ACTIVE" in desired.values():
        proof = await launch._preflight(client, web, payload, target, publication_required=True)
        if proof["payment_selection_required"]:
            raise fail("PAYMENT_SELECTION_REQUIRED", "Choose the fresh linked payment method before activation")
    else:
        row = await client.get("/act_" + target["ad_account_id"], {"fields": ["id", "account_id", "business"]})
        if (not isinstance(row.get("id"), str) or row["id"].removeprefix("act_") != target["ad_account_id"]
                or not isinstance(row.get("business"), dict) or row["business"].get("id") != target["business_id"]):
            raise fail("DELIVERY_SCOPE_MISMATCH", "Delivery account did not confirm its exact Business")
    rows = await _chain(client, entities, target["page_id"], bindings)
    sender = AdsManagerSubmitter(state, launch_key=key, profile_id=profile_id,
        business_id=target["business_id"], account_id=target["ad_account_id"], actor_id=actor,
        launch_fingerprint=fingerprint, source_digest=launch.source_digest)
    publisher = AdsManagerDraftPublisher(sender)
    order = ("ad", "adset", "campaign") if "ACTIVE" in desired.values() else ("campaign", "adset", "ad")
    completed = []
    for section in order:
        operation = "DELIVERY_" + section.upper()
        prior = await state.step(key, OperationKey("ADS_MANAGER:" + operation + "_FRAGMENT"))
        published = await state.step(key, OperationKey("ADS_MANAGER:" + operation + "_PUBLISH"))
        if published is not None and prior is None:
            raise fail("SAVED_OPERATION_MISMATCH", "Delivery publication has no saved status fragment")
        saved = (prior or {}).get("result", {}).get("evidence", {}).get("receipt", {})
        old = saved.get("old_status") if prior else rows[section]["status"]
        if not prior and old == desired[section]:
            continue
        if old not in {"ACTIVE", "PAUSED"}:
            raise fail("RECONCILE_REQUIRED", "Delivery fragment has an unknown reply; replay is blocked")
        level = "ad_set" if section == "adset" else section
        params = fragment_params(draft_id, level=level, action="modify", object_id=entities[section + "_id"],
                                 values={"status": desired[section]})
        params["values"][0]["old_value"] = old
        receipt = await sender.submit(client, operation=operation + "_FRAGMENT",
            path="/" + draft_id + "/addraft_fragments", params=params,
            receipt=lambda row: {"fragment_id": identity(row.get("id")), "old_status": old},
            saved_only=published is not None)
        if published is not None:
            # Accepted publication may consume the temporary fragment. Its
            # confirmed journal binds the exact request; current proof comes
            # from publication status and independent object reads below.
            verified = (prior or {}).get("result", {}).get("evidence", {}).get("verification", {})
            if (not prior or prior.get("status") != "SUCCESS" or verified.get("verified") is not True
                    or verified.get("source") != "independent_private_ads_http"
                    or verified.get("fragment_id") != receipt["fragment_id"]
                    or verified.get("meta_validation_status") != "VALIDATED"):
                raise fail("SAVED_OPERATION_MISMATCH", "Delivery publication has no validated matching status fragment")
        else:
            fragment = await client.get("/addraftfragment_" + receipt["fragment_id"], {"fields": FRAGMENT_FIELDS})
            if (fragment.get("id") != receipt["fragment_id"] or fragment.get("account_id") != target["ad_account_id"]
                    or fragment.get("ad_draft_id") != draft_id or fragment.get("ad_object_id") != entities[section + "_id"]
                    or fragment.get("ad_object_type") != level):
                raise fail("DELIVERY_FRAGMENT_MISMATCH", "Status fragment does not belong to the saved advertising object")
            if not fragment_values_match(fragment.get("values"), {"status": desired[section]},
                    business_id=target["business_id"], old_values={"status": old}):
                raise fail("DELIVERY_FRAGMENT_MISMATCH", "Status fragment differs from the requested delivery")
            if fragment.get("active_errors") or fragment.get("validation_status") == "HAS_ERRORS":
                raise fail("VALIDATION_ERRORS", "Meta rejected the " + section + " delivery fragment")
            if fragment.get("validation_status") != "VALIDATED":
                raise fail("VALIDATION_PENDING", "Meta has not validated the " + section + " delivery fragment; resume its saved read", True)
            await publisher._confirm(operation + "_FRAGMENT", {"fragment_id": receipt["fragment_id"],
                "meta_validation_status": "VALIDATED"})
        publication = await sender.submit(client, operation=operation + "_PUBLISH",
            path="/" + draft_id + "/publish", params=publish_params([receipt["fragment_id"]]),
            receipt=publication_receipt, saved_only=published is not None)
        for attempt in range(12):
            response = await client.get("/addraft_" + draft_id + "/addraft_publish_statuses",
                {"async_request_set_ids": [publication["operation_id"]], "fields": PUBLISH_FIELDS, "date_format": "U"})
            progress = publication_status(response, operation_id=publication["operation_id"], fragment_ids=[receipt["fragment_id"]])
            if progress["completed"]:
                break
            if attempt < 11:
                await asyncio.sleep(3)
        else:
            raise fail("PUBLISH_PENDING", "Saved delivery publication is still processing; resume its reads", True)
        rows = await _chain(client, entities, target["page_id"], bindings)
        if rows[section]["status"] != desired[section]:
            raise fail("DELIVERY_UNCONFIRMED", "Independent read has not confirmed the requested state", True)
        await publisher._confirm(operation + "_PUBLISH", {"entity_id": entities[section + "_id"], "status": desired[section]})
        completed.append({"step": operation, "status": "SUCCESS", "entity_id": entities[section + "_id"]})
    rows = await _chain(client, entities, target["page_id"], bindings)
    if any(rows[section]["status"] != desired[section] for section in desired):
        raise fail("DELIVERY_UNCONFIRMED", "Final delivery states differ from this request", True)
    return {"status": "SUCCESS", "verified": True, "entities": entities, **target,
            "delivery": desired, "effective_statuses": {section: rows[section].get("effective_status")
                for section in ("campaign", "adset", "ad")}, "delivery_running_verified": False,
            "steps": completed, "source_launch_key": source_key,
            "delivery_key": key, "browser_started": False, "transport": "private_ads_manager_http_h2"}
