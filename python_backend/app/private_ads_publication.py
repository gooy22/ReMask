"""Create three draft levels and resume their publication from saved receipts.

The ad's creative is embedded in its draft value. The fourth Creative ID is
obtained by independent published-object inspection, not guessed from a draft.
"""
from __future__ import annotations

import copy
import json
import asyncio
import time

from .action_result import ActionResult
from .private_ads_draft import (FRAGMENT_FIELDS, LEVELS, PUBLISH_FIELDS, fail,
                                fragment_params, fragment_values_match, identity, publication_receipt, publication_status, publish_params)
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

    async def run(self, client, *, name, levels, verify_objects, draft_only=False):
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
        if draft_only and await self.submitter.state.step(self.submitter.launch_key, OperationKey("ADS_MANAGER:PUBLISH")):
            raise fail("SAVED_OPERATION_MISMATCH", "An accepted publication cannot be relabelled as an unpublished draft")
        recovery = await self._saved_publication(client, name, levels)
        if recovery is not None:
            draft_id, fragments, objects, publication = recovery
        else:
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
                if not fragment_values_match(row.get("values"), values, business_id=scope["business_id"]):
                    raise fail("READ_SPEC_MISMATCH", "Saved draft settings differ from this Launch")
                # Meta can expose HAS_ERRORS before returning its error list.
                # A stored fragment is not a settled, valid configuration.
                if row.get("active_errors") or row.get("validation_status") == "HAS_ERRORS":
                    raise fail("VALIDATION_ERRORS", "Meta returned active validation errors for the " + level + " draft")
                if row.get("validation_status") != "VALIDATED":
                    # Keep the accepted receipt. A retry reads this exact
                    # fragment through the original intent, without POST replay.
                    raise fail("VALIDATION_PENDING", "Meta has not confirmed validation of the " + level + " draft; resume its saved read", True)
                objects[level] = identity(row.get("ad_object_id"))
                if objects[level] in list(objects.values())[:-1]:
                    raise fail("READ_RELATION_MISMATCH", "Draft levels have duplicate object identities")
                await self._confirm(operation, {"draft_id": draft_id, "fragment_id": fragment_id,
                    "ad_object_id": objects[level], "fragment_saved": True,
                    "relationships_confirmed": True, "editor_verified": False,
                    "meta_validation_status": row.get("validation_status"),
                    "active_errors_observed": "active_errors" in row})
                fragments.append(fragment_id);parent_id = objects[level]
            if draft_only:
                return {"status": "SUCCESS", "draft_id": draft_id, "fragment_ids": fragments,
                    "draft_objects": objects, "draft_verified": True, "published": False,
                    "fragment_storage_verified": True, "relationships_confirmed": True,
                    "editor_verified": False,
                    "verified": False, "delivery_running_verified": False, "browser_started": False,
                    "verification_source": "independent_private_ads_http",
                    "transport": "private_ads_manager_http_h2"}
            publication = await self.submitter.submit(client, operation="PUBLISH",
                path="/" + draft_id + "/publish", params=publish_params(fragments),
                receipt=publication_receipt)
        for attempt in range(12):
            response = await client.get("/addraft_" + draft_id + "/addraft_publish_statuses",
                {"async_request_set_ids": [publication["operation_id"]], "fields": PUBLISH_FIELDS, "date_format": "U"})
            progress = publication_status(response, operation_id=publication["operation_id"], fragment_ids=fragments)
            await self._progress(progress)
            if progress["completed"]:
                break
            if attempt < 11:
                await asyncio.sleep(3)
        else:
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
                "effective_statuses": proof.get("effective_statuses", {}), "delivery_running_verified": False,
                "browser_started": False, "transport": "private_ads_manager_http_h2"}

    async def _saved_publication(self, client, name, levels):
        """A publish receipt resumes only status/object READs, never draft POSTs.

        Confirmed draft identities are lookup references, not current object
        proof. They need not remain available after publication consumes them.
        Every request fingerprint is still checked against the durable intent.
        """
        state, launch_key = self.submitter.state, self.submitter.launch_key
        if not await state.step(launch_key, OperationKey("ADS_MANAGER:PUBLISH")):
            return None
        scope = self.submitter.target
        confirmed = {}
        for operation in ("DRAFT", "FRAGMENT_CAMPAIGN", "FRAGMENT_AD_SET", "FRAGMENT_AD"):
            row = await state.step(launch_key, OperationKey("ADS_MANAGER:" + operation))
            evidence = (row or {}).get("result", {}).get("evidence", {})
            proof = evidence.get("verification", {})
            if (not row or row.get("status") != "SUCCESS" or proof.get("verified") is not True
                    or proof.get("source") != "independent_private_ads_http"
                    or any(str(row.get(key)) != str(value) for key, value in scope.items())
                    or evidence.get("actor_id") != self.submitter.actor_id
                    or evidence.get("launch_fingerprint") != self.submitter.launch_fingerprint
                    or evidence.get("source_digest") != self.submitter.source_digest):
                raise fail("SAVED_OPERATION_MISMATCH", "Publication recovery has no confirmed matching draft hierarchy")
            confirmed[operation] = proof
        draft = await self.submitter.submit(client, operation="DRAFT",
            path="/act_" + scope["ad_account_id"] + "/addrafts",
            params={"name": name, "use_active_draft_if_exists": True,
                    "use_employee_draft": False, "application_id": client.context.application_id,
                    "ownership_type": "USER"}, receipt=lambda row: row, saved_only=True)
        draft_id = identity(draft.get("draft_id"))
        if confirmed["DRAFT"].get("draft_id") != draft_id:
            raise fail("SAVED_OPERATION_MISMATCH", "Saved draft identity differs from its confirmation")
        fragments, objects, parent_id = [], {}, None
        for level in LEVELS:
            operation = "FRAGMENT_" + level.upper()
            proof = confirmed[operation]
            values = copy.deepcopy(levels[level])
            if level == "ad_set":
                values["campaign_id"] = objects["campaign"]
            if level == "ad":
                values.update(campaign_id=objects["campaign"], adset_id=objects["ad_set"])
            receipt = await self.submitter.submit(client, operation=operation,
                path="/" + draft_id + "/addraft_fragments",
                params=fragment_params(draft_id, level=level, values=values, parent_id=parent_id),
                receipt=lambda row: row, saved_only=True)
            fragment = identity(receipt.get("fragment_id"))
            entity = identity(proof.get("ad_object_id"))
            if (proof.get("draft_id") != draft_id or proof.get("fragment_id") != fragment
                    or entity in objects.values() or fragment in fragments):
                raise fail("SAVED_OPERATION_MISMATCH", "Saved publication hierarchy is inconsistent")
            fragments.append(fragment)
            objects[level] = parent_id = entity
        publication = await self.submitter.submit(client, operation="PUBLISH",
            path="/" + draft_id + "/publish", params=publish_params(fragments),
            receipt=lambda row: row, saved_only=True)
        identity(publication.get("operation_id"))
        return draft_id, fragments, objects, publication

    async def _progress(self, progress):
        state, launch_key = self.submitter.state, self.submitter.launch_key
        prior = await state.step(launch_key, OperationKey("ADS_MANAGER:PUBLISH"))
        if not prior:
            raise fail("RECONCILE_REQUIRED", "Publication progress has no saved operation")
        evidence = copy.deepcopy(prior["result"].get("evidence", {}))
        # Do not downgrade a confirmed publication on repeat or stale reads.
        if prior["status"] == "SUCCESS" and evidence.get("verification", {}).get("verified") is True:
            saved = evidence.get("publication", {})
            if saved.get("operation_id") != progress["operation_id"]:
                raise fail("SAVED_OPERATION_MISMATCH", "Publication belongs to another operation")
            if not progress["completed"]:
                raise fail("PUBLISH_STATUS_REGRESSION", "Confirmed publication returned pending status", True)
            return
        old = evidence.get("publication", {})
        evidence["publication"] = {"operation_id": progress["operation_id"],
            "status": progress["status"], "code": "PUBLISH_COMPLETED" if progress["completed"] else "PUBLISH_PENDING",
            "poll_count": int(old.get("poll_count", 0)) + 1, "last_polled_at": int(time.time())}
        evidence.pop("verification", None)
        result = ActionResult.reconcile(prior["step"], code="PRIVATE_ADS_DRAFT_OBJECTS_UNVERIFIED"
            if progress["completed"] else "PRIVATE_ADS_DRAFT_PUBLISH_PENDING",
            message="Publication accepted; independent object verification pending", submitted=True, evidence=evidence)
        result.entity_id = prior.get("entity_id") or progress["operation_id"]
        if not await state.finish_read(prior, result):
            raise fail("READ_PENDING", "Another worker updated publication progress", True)
