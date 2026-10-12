"""Durable private Ads Manager POST receipts on the existing Launch journal.

The sender claims immediately before dispatch. A lost reply cannot replay POST;
a saved receipt is resumed by READ and is never treated as verified creation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from .action_result import ActionResult
from .private_ads_draft import fail, flatten_params
from .private_launch_extensions import OperationKey


class AdsManagerSubmitter:
    def __init__(self, state, *, launch_key, profile_id, business_id, account_id, actor_id,
                 launch_fingerprint, source_digest):
        self.state, self.launch_key = state, launch_key
        self.target = {"profile_id": profile_id, "business_id": business_id,
                       "ad_account_id": account_id}
        self.launch_fingerprint, self.source_digest = launch_fingerprint, source_digest
        self.actor_id = actor_id

    async def submit(self, client, *, operation, path, params, receipt, files=None,
                     media_digest=""):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,50}(?::[a-f0-9]{64})?", operation):
            raise fail("INVALID_OPERATION", "Advertising journal operation is malformed")
        params = copy.deepcopy(params)
        key = OperationKey("ADS_MANAGER:" + operation)
        # Encoding before a claim catches invalid values without poisoning
        # the no-replay journal. Credentials are exclusively client memory.
        encoded = flatten_params(params)
        fingerprint = hashlib.sha256(json.dumps({"target": self.target,
            "launch": self.launch_fingerprint, "source": self.source_digest, "actor_id": self.actor_id,
            "path": path, "params": encoded, "media_digest": media_digest},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        prior = await self.state.step(self.launch_key, key)
        if prior:
            evidence = prior.get("result", {}).get("evidence", {})
            if (any(str(prior.get(name)) != str(value) for name, value in self.target.items())
                    or evidence.get("operation_fingerprint") != fingerprint):
                raise fail("SAVED_OPERATION_MISMATCH", "Saved advertising submission has another target or configuration")
            saved = evidence.get("receipt")
            if isinstance(saved, dict) and saved:
                return copy.deepcopy(saved)
            raise fail("RECONCILE_REQUIRED", "Advertising submission has no known reply; automatic replay is blocked")
        evidence = {"operation_fingerprint": fingerprint,
                    "launch_fingerprint": self.launch_fingerprint,
                    "source_digest": self.source_digest, "actor_id": self.actor_id, "receipt": {}}
        intent = ActionResult.reconcile(key.value, code="PRIVATE_ADS_DRAFT_SUBMITTING",
            message="Advertising submission has a durable intent", evidence=evidence)
        claimed = False

        async def claim():
            nonlocal claimed
            await self.state.save(launch_key=self.launch_key, **self.target, step=key,
                status="SUBMITTING", result=intent.as_dict(), error_code=intent.code, submitted=None)
            claimed = True

        try:
            response = await client.post(path, params, before_submit=claim, files=files)
        except Exception:
            if claimed:
                raise fail("RECONCILE_REQUIRED", "Advertising response was lost; resume requires independent inspection") from None
            raise
        if not claimed:
            raise fail("SUBMIT_CLAIM_REQUIRED", "Advertising sender did not claim its submission")
        try:
            result = receipt(response)
            allowed = {"draft_id", "fragment_id", "ad_object_id", "operation_id", "image_hash", "video_id"}
            if (not isinstance(result, dict) or not result or set(result) - allowed
                    or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value)
                           for value in result.values())):
                raise ValueError("Invalid receipt")
        except Exception:
            raise fail("RECONCILE_REQUIRED", "Advertising response has no complete usable receipt; replay is blocked") from None
        evidence["receipt"] = result
        pending = ActionResult.reconcile(key.value, code="PRIVATE_ADS_DRAFT_READ_PENDING",
            message="Advertising references saved; independent verification is pending",
            submitted=True, evidence=evidence)
        pending.entity_id = next(iter(result.values()))
        prior = await self.state.step(self.launch_key, key)
        if not prior or not await self.state.finish_read(prior, pending):
            raise fail("READ_PENDING", "Another worker updated advertising progress; resume its saved state", True)
        return copy.deepcopy(result)
