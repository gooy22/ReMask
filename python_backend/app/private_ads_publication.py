"""Create three draft levels and resume their publication from saved receipts.

The ad's creative is embedded in its draft value. The fourth Creative ID is
obtained by independent published-object inspection, not guessed from a draft.
"""
from __future__ import annotations

import copy
import json

from .action_result import ActionResult
from .private_ads_draft import (FRAGMENT_FIELDS, LEVELS, PUBLISH_FIELDS, fail,
                                fragment_params, identity, publication_status, publish_params)
from .private_launch_extensions import OperationKey


class AdsManagerDraftPublisher:
    def __init__(self, submitter):
        self.submitter = submitter

    async def _confirm(self, operation, proof):
        state, launch_key = self.submitter.state, self.submitter.launch_key
        prior = await state.step(launch_key, OperationKey("ADS_MANAGER:" + operation))
        if not prior:
            raise fail("RECONCILE_REQUIRED", "Draft verification has no saved submission")
        evidence = copy.deepcopy(prior.get("result", {}).get("evidence", {}))
        evidence["verification"] = {"verified": True, "source": "independent_private_ads_http", **proof}
        result = ActionResult.success(prior["step"], entity_id=prior.get("entity_id") or "",
            code="PRIVATE_ADS_DRAFT_READ_CONFIRMED", evidence=evidence)
        if not await state.finish_read(prior, result):
            raise fail("READ_PENDING", "Another worker updated draft verification", True)

    @staticmethod
    def _account(row, account_id):
        if (not isinstance(row, dict) or row.get("error") or row.get("errors")
                or not isinstance(row.get("account_id"), str)
                or row["account_id"].removeprefix("act_") != account_id):
            raise fail("READ_SCOPE_MISMATCH", "Draft response belongs to another advertising account")

    async def run(self, client, *, name, levels, verify_objects):
        if not isinstance(levels, dict) or set(levels) != set(LEVELS):
            raise fail("INVALID_INPUT", "Publication requires Campaign, AdSet and Ad draft values")
        # Validate every level before the first create. Do not let malformed
        # downstream input leave a partially created draft hierarchy.
        levels = copy.deepcopy(levels)
        for level in LEVELS:
            fragment_params("100000", level=level, values=levels[level])
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise fail("INVALID_INPUT", "Draft name is missing or too long")
        scope = self.submitter.target
        if (client.context.account_id != scope["ad_account_id"]
                or client.context.business_id != scope["business_id"]
                or client.context.actor_id != self.submitter.actor_id):
            raise fail("SAVED_OPERATION_MISMATCH", "Draft HTTP session belongs to another target")
        draft = await self.submitter.submit(client, operation="DRAFT",
            path="/act_" + scope["ad_account_id"] + "/addrafts",
            params={"name": name, "use_active_draft_if_exists": True,
                    "use_employee_draft": False, "application_id": client.context.application_id,
                    "ownership_type": "USER"}, receipt=lambda row: {"draft_id": identity(row.get("id"))})
        draft_id = draft["draft_id"]
        row = await client.get("/addraft_" + draft_id,
                               {"fields": ["id", "account_id", "author_id", "ownership_type"]})
        self._account(row, scope["ad_account_id"])
        if row.get("id") != draft_id or row.get("author_id") != client.context.actor_id or row.get("ownership_type") != "USER":
            raise fail("READ_SCOPE_MISMATCH", "Draft identity or ownership is unconfirmed")
        await self._confirm("DRAFT", {"draft_id": draft_id})
        fragments, objects, parent_id = [], {}, None
        for level in LEVELS:
            values = copy.deepcopy(levels[level])
            if level == "ad_set":
                values["campaign_id"] = objects["campaign"]
            if level == "ad":
                values.update({"campaign_id": objects["campaign"], "adset_id": objects["ad_set"]})
            params = fragment_params(draft_id, level=level, values=values, parent_id=parent_id)
            operation = "FRAGMENT_" + level.upper()
            receipt = await self.submitter.submit(client, operation=operation,
                path="/" + draft_id + "/addraft_fragments", params=params,
                receipt=lambda row: {"fragment_id": identity(row.get("id"))})
            fragment_id = receipt["fragment_id"]
            row = await client.get("/addraftfragment_" + fragment_id, {"fields": FRAGMENT_FIELDS})
            self._account(row, scope["ad_account_id"])
            if (row.get("id") != fragment_id or row.get("ad_draft_id") != draft_id
                    or row.get("ad_object_type") != level
                    or (parent_id is not None and row.get("parent_ad_object_id") != parent_id)):
                raise fail("READ_RELATION_MISMATCH", "Draft fragment identity or parent is unconfirmed")
            observed = row.get("values")
            if not isinstance(observed, list) or any(not isinstance(entry, dict) for entry in observed):
                raise fail("READ_INCOMPLETE", "Draft fragment values are unavailable", True)
            fields = [entry.get("field") for entry in observed]
            if any(not isinstance(field, str) for field in fields) or len(set(fields)) != len(fields):
                raise fail("READ_INCOMPLETE", "Draft fragment fields are ambiguous", True)
            decoded = {}
            try:
                for entry in observed:
                    value = entry.get("new_value")
                    decoded[entry["field"]] = json.loads(value) if isinstance(value, str) else value
            except (TypeError, ValueError):
                raise fail("READ_INCOMPLETE", "Draft fragment values cannot be decoded", True) from None
            if any(field not in decoded or decoded[field] != value for field, value in values.items()):
                raise fail("READ_SPEC_MISMATCH", "Saved draft settings differ from this Launch")
            objects[level] = identity(row.get("ad_object_id"))
            if objects[level] in list(objects.values())[:-1]:
                raise fail("READ_RELATION_MISMATCH", "Draft levels have duplicate object identities")
            await self._confirm(operation, {"draft_id": draft_id, "fragment_id": fragment_id,
                "ad_object_id": objects[level]})
            fragments.append(fragment_id);parent_id = objects[level]
        publication = await self.submitter.submit(client, operation="PUBLISH",
            path="/" + draft_id + "/publish", params=publish_params(fragments),
            receipt=lambda row: {"operation_id": identity(row.get("async_request_set_id"))})
        response = await client.get("/addraft_" + draft_id + "/addraft_publish_statuses",
            {"async_request_set_ids": [publication["operation_id"]], "fields": PUBLISH_FIELDS, "date_format": "U"})
        progress = publication_status(response, operation_id=publication["operation_id"], fragment_ids=fragments)
        if not progress["completed"]:
            raise fail("PUBLISH_PENDING", "Meta is processing the saved publication; retry resumes its reads", True)
        proof = await verify_objects(client, copy.deepcopy(objects), copy.deepcopy(levels))
        ids = proof.get("entities") if isinstance(proof, dict) else None
        required = {"campaign_id", "adset_id", "creative_id", "ad_id"}
        if not isinstance(proof, dict) or proof.get("verified") is not True or not isinstance(ids, dict) or set(ids) != required:
            raise fail("OBJECTS_UNVERIFIED", "Publication completed; independent object proof is pending", True)
        clean = {key: identity(value) for key, value in ids.items()}
        if (len(set(clean.values())) != 4 or clean["campaign_id"] != objects["campaign"]
                or clean["adset_id"] != objects["ad_set"] or clean["ad_id"] != objects["ad"]):
            raise fail("READ_RELATION_MISMATCH", "Published objects differ from the saved draft hierarchy")
        await self._confirm("PUBLISH", {"operation_id": publication["operation_id"], "entities": clean})
        return {"status": "SUCCESS", "entities": clean, "draft_id": draft_id,
                "publication_id": publication["operation_id"], "verified": True,
                "browser_started": False, "transport": "private_ads_manager_http_h2"}
