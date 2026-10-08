"""Production BM/RK actions: private HTTP precheck, submit, verify and commit.

No browser factory, UI controller or coordinate operation belongs here. The
legacy UI handlers remain isolated for compatibility with historical traces.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from ..facebook_ad_account_create import create_ad_account_with_docids
from ..static_meta_contracts import create_business_static as create_business_with_docids, rk_create_contract
from ..static_meta_inventory import read_business_inventory, read_ad_account_inventory
from ..private_inventory import (
    _auth_gate, _extract_business_inventory_rows, _json_payloads,
)
from .models import ProvisioningError, ProvisioningStep
from .state import ProvisioningStateStore

log = logging.getLogger("remask_worker")
_PENDING = {"CREATE_CLICK_INTENT", "CREATE_PENDING_SUBMIT", "CREATE_SUBMIT_INTENT",
            "CREATE_SUBMITTED", "CREATE_RESULT_UNKNOWN", "CREATE_RESULT_UNVERIFIED", "RECONCILE_CREATE"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _id(value: Any) -> str:
    value = _clean(value).removeprefix("act_")
    return value if re.fullmatch(r"\d{5,30}", value) else ""


def _has_resume(saved):
    return (_clean(saved.get("phase")).upper() in _PENDING
        or any(_id(saved.get(key)) for key in ("business_id", "create_response_business_id",
            "ad_account_id", "create_response_ad_account_id")))


class _Action:
    def __init__(self, session, state, params, kwargs, step):
        self.session = kwargs.get("meta_transport") or session
        self.context = self.session.context
        self.profile = _clean(kwargs.get("profile_id") or state.get("profile_id") or self.context.profile_id)
        self.scope = _clean(kwargs.get("scope_key") or state.get("scope_key") or params.get("scope_key") or "default")
        self.item = _clean(kwargs.get("item_id"))
        self.store = kwargs.get("provisioning_state")
        self.step = step
        self.prior = kwargs.get("step_state")
        self.saved = {}
        if not self.profile or not self.item or not isinstance(self.store, ProvisioningStateStore):
            raise ProvisioningError("INTERNAL_STATE_ERROR", "Private action requires durable profile/job state.")

    async def open(self):
        prior = self.prior if isinstance(self.prior, dict) else await self.store.step(self.item, self.step)
        self.saved = dict((prior or {}).get("result") or {})
        self.web = await self.session.facebook_web()
        self.web.private_only = True
        current = _clean((getattr(self.context, 'cookies', {}) or {}).get('c_user'))
        bootstrap = await self.web.bootstrap()
        if not current.isdigit() or _clean(getattr(bootstrap, 'actor_id', '')) != current:
            raise ProvisioningError('SESSION_EXPIRED', 'Static action actor does not match the selected profile; no POST was sent.', retryable=True)
        return self

    async def checkpoint(self, patch):
        self.saved = await self.store.checkpoint(self.item, self.profile, self.scope, self.step,
            {"transport": "private_http", "browser_started": False, "activity_at": int(time.time()), **patch})
        historical = bool(patch.get("recovered_from_item_id"))
        log.info("[%s] %s private stage=%s business=%s checkpoint_origin=%s browser_started=False", self.profile, self.step.value,
            "RESTORE_PREVIOUS_CHECKPOINT" if historical else patch.get("phase", patch.get("activity", "checkpoint")),
            self.saved.get("business_id", ""), "history" if historical else "current")

    async def commit(self, result):
        # Inventory proof precedes the first confirmed entity write.
        result = {"create_response_path": self.saved.get("create_response_path", ""),
            "private_inventory_verified": True, "post_create_verified": True, **result}
        if self.step is ProvisioningStep.BUSINESS:
            result.setdefault("create_response_business_id", self.saved.get("create_response_business_id", ""))
            result["recovered_after_create_uncertainty"] = not bool(result["create_response_business_id"])
        else:
            await self.store.remember_entity(self.profile, self.scope, ProvisioningStep.BUSINESS,
                {"business_id": result["business_id"]})
        await self.checkpoint({**result, "phase": "CREATE_CONFIRMED", "resume_from": "DONE"})
        await self.store.remember_entity(self.profile, self.scope, self.step, result)
        return {**result, "phase": "CREATE_CONFIRMED", "transport": "private_http", "browser_started": False}


def _business_collection_complete(payload):
    if isinstance(payload, dict) and (payload.get('errors') or payload.get('error')):
        return False
    observations = []
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                compact = re.sub(r"[^a-z0-9]", "", key.lower())
                if compact in {"businesses", "businessportfolios", "businessmanagers"}:
                    info = child.get("page_info", child.get("pageInfo", {})) if isinstance(child, dict) else {}
                    items = child if isinstance(child, list) else next((child.get(field) for field in ('edges', 'nodes', 'items')
                        if isinstance(child, dict) and isinstance(child.get(field), list)), None)
                    valid = isinstance(items, list) and all(
                        isinstance(item, dict) and len(_extract_business_inventory_rows(item)) == 1
                        for item in items)
                    observations.append(valid and (isinstance(child, list) or bool(isinstance(info, dict)
                        and info.get("has_next_page", info.get("hasNextPage")) is False
                        and info.get("has_previous_page", info.get("hasPreviousPage", False)) is False
                        and any(isinstance(child.get(field), list) for field in ("edges", "nodes", "items")))))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    return bool(observations) and all(observations)


async def _business_inventory(web, expected=""):
    try:
        return await read_business_inventory(web, expected)
    except Exception as exc:
        from ..private_auth import private_auth_error
        error = private_auth_error(exc)
        if error is not None:
            raise error from exc
        raise


async def _rk_inventory(web, business, name, expected=""):
    try:
        return await asyncio.wait_for(read_ad_account_inventory(web, business, name, expected), timeout=90)
    except Exception as exc:
        from ..private_auth import private_auth_error
        error = private_auth_error(exc)
        if error is not None:
            error.inventory_diagnostics = [{"operation": "READ_RK", **row} for row in error.inventory_diagnostics]
            raise error from exc
        raise


async def _verify_rk(action, business, name, expected):
    for attempt in range(3):
        try:
            proof = await _rk_inventory(action.web, business, name, expected)
            if proof["id"]:
                return proof
        except ProvisioningError as exc:
            if getattr(exc, "inventory_diagnostics", None):
                await action.checkpoint({"inventory_verification": exc.inventory_diagnostics,
                    "inventory_auth_error": exc.code})
            raise
        except Exception as exc:
            log.info("RK private verify business=%s error_type=%s", business, type(exc).__name__)
        if attempt < 2:
            await asyncio.sleep(0.4 * (attempt + 1))
    return None


async def business_handler(session, params, state, *args, **kwargs):
    action = await _Action(session, state, params, kwargs, ProvisioningStep.BUSINESS).open()
    name = _clean(params.get("name") or params.get("bm_name"))
    if not name or len(name) > 255:
        raise ProvisioningError("INVALID_INPUT", "BUSINESS.name is required and must fit 255 characters.")
    legacy = re.fullmatch(r"ReMask_BM_(\d+)", name, re.I)
    if legacy:
        name = "ReMask Business " + legacy.group(1)
    if params.get("attach_page") is True:
        raise ProvisioningError("BUSINESS_PAGE_ACCESS_SEPARATE", "Add existing Page must run as a separate PAGE_ACCESS action after BM/RK verification.")
    email = _clean(params.get("user_email") or params.get("email") or getattr(action.context, "email", ""))
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ProvisioningError("BUSINESS_EMAIL_REQUIRED", "A valid BUSINESS.user_email is required.")
    if not _has_resume(action.saved):
        previous = await action.store.latest_business_resume_for_name(action.profile, name, exclude_item_id=action.item)
        if previous:
            await action.checkpoint({**previous["result"], "recovered_from_item_id": previous["item_id"]})
    if action.saved.get("business_name") and _clean(action.saved["business_name"]).casefold() != name.casefold():
        raise ProvisioningError("BUSINESS_CHECKPOINT_MISMATCH", "Saved CREATE belongs to another BM name.")
    expected = _id(action.saved.get("business_id") or action.saved.get("create_response_business_id") or state.get("business_id"))
    before = await asyncio.wait_for(_business_inventory(action.web, expected), timeout=30)
    await action.checkpoint({"activity": "BM_PRIVATE_INVENTORY_PRECHECK",
        "inventory_verification": before.get("diagnostics", []), "inventory_complete": before["complete"]})
    named = [business for business, row_name in before["rows"].items() if row_name.casefold() == name.casefold()]
    confirmed = expected if expected in before["rows"] else (named[0] if not expected and before["complete"] and len(named) == 1 else "")
    pending = _clean(action.saved.get("phase")).upper() in _PENDING
    # An uncertain name-only CREATE requires evidence that this was absent in
    # the complete pre-submit response, rather than attributing an old BM to it.
    if pending and not expected and not (action.saved.get("baseline_complete") is True
            and confirmed and confirmed not in action.saved.get("baseline_business_ids", [])):
        confirmed = ""
    if confirmed:
        return await action.commit({"business_id": confirmed, "business_name": name,
            "verification": {"source": before["source"], "exact_business_id": confirmed}, "recovered": True})
    if expected or pending:
        await action.checkpoint({"phase": "CREATE_RESULT_UNKNOWN", "business_name": name})
        raise ProvisioningError("CREATE_BM_RESULT_UNKNOWN", "Previous BM CREATE is retained; fresh HTTP inventory has not confirmed its result. No duplicate POST was sent.", retryable=True)
    if len(named) > 1 or not before["complete"]:
        raise ProvisioningError("PRIVATE_BM_INVENTORY_INCONCLUSIVE", "Private BM inventory did not prove a unique target or complete absence. No CREATE was sent.", retryable=True)
    await action.checkpoint({"phase": "CREATE_NOT_SUBMITTED", "business_name": name,
        "baseline_complete": True, "baseline_business_ids": sorted(before["rows"]), "business_id": ""})
    submitted = False
    async def before_submit():
        nonlocal submitted
        await action.checkpoint({"phase": "CREATE_SUBMIT_INTENT", "activity": "BM_PRIVATE_POST_INTENT"})
        submitted = True
    try:
        created = await create_business_with_docids(action.web, business_name=name, user_email=email,
            user_first_name=_clean(params.get("user_first_name") or params.get("first_name") or getattr(action.context, "first_name", "")),
            user_last_name=_clean(params.get("user_last_name") or params.get("last_name") or getattr(action.context, "last_name", "")),
            profile_display_name=_clean(params.get("profile_display_name") or getattr(action.context, "display_name", "")),
            manual_doc_id=_clean(params.get("doc_id")), profile_id=action.profile, before_submit=before_submit)
        expected = _id(created.business_id)
        await action.checkpoint({"phase": "CREATE_RESULT_UNVERIFIED", "create_response_business_id": expected,
            "create_response_path": created.response_path})
    except Exception as exc:
        payload = getattr(exc, "payload", getattr(exc, "meta_payload", {}))
        rejected = isinstance(payload, dict) and bool(payload.get("errors") or payload.get("error")) and not payload.get("data")
        if not submitted or getattr(exc, "request_may_have_been_sent", None) is False or rejected:
            await action.checkpoint({"phase": "CREATE_REJECTED" if rejected else "CREATE_NOT_SUBMITTED"})
            raise ProvisioningError(getattr(exc, "code", "PRIVATE_BM_NOT_SUBMITTED"), str(exc), retryable=bool(getattr(exc, "retryable", True))) from exc
        await action.checkpoint({"phase": "CREATE_RESULT_UNKNOWN"})
    for attempt in range(3):
        proof = await asyncio.wait_for(_business_inventory(action.web, expected), timeout=30)
        matches = [business for business, row_name in proof["rows"].items()
            if row_name.casefold() == name.casefold() and business not in before["rows"]]
        found = expected if expected in proof["rows"] else (matches[0] if not expected and len(matches) == 1 else "")
        if found:
            return await action.commit({"business_id": found, "business_name": name,
                "create_response_business_id": expected, "verification": {"source": proof["source"], "exact_business_id": found}})
        if attempt < 2:
            await asyncio.sleep(0.4 * (attempt + 1))
    raise ProvisioningError("CREATE_BM_RESULT_UNKNOWN", "BM submit is retained; independent HTTP inventory has not confirmed it. Retry verifies before any POST.", retryable=True)


async def ad_account_handler(session, params, state, *args, **kwargs):
    action = await _Action(session, state, params, kwargs, ProvisioningStep.AD_ACCOUNT).open()
    business = _id(params.get("business_id") or params.get("bm_id") or state.get("business_id"))
    name = _clean(params.get("name") or params.get("rk_name"))
    currency = _clean(params.get("currency")).upper()
    try:
        timezone = int(params["timezone_id"])
    except (KeyError, TypeError, ValueError):
        raise ProvisioningError("INVALID_INPUT", "AD_ACCOUNT.timezone_id must be an integer.")
    if not business or not name or len(name) > 255 or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ProvisioningError("INVALID_INPUT", "Exact BM, RK name and currency are required.")
    actor = _clean((getattr(action.context, "cookies", {}) or {}).get("c_user"))
    if not actor.isdigit():
        raise ProvisioningError("SESSION_EXPIRED", "Current profile actor is unavailable.", retryable=True)
    if business == actor:
        raise ProvisioningError("CREATED_BUSINESS_REQUIRED", "RK creation requires a Business Portfolio, not the personal profile scope.")
    if not (_clean(action.saved.get("phase")).upper() in _PENDING
            or _id(action.saved.get("ad_account_id") or action.saved.get("create_response_ad_account_id"))):
        # All names matter: a different RK name must not evade a pending POST
        # when the normal contract allows exactly one RK per Business.
        previous = await action.store.latest_private_ad_account_resume(action.profile, business,
            exclude_item_id=action.item, account_name=name if params.get("allow_multiple_in_business") is True else "")
        if previous:
            await action.checkpoint({**previous["result"], "recovered_from_item_id": previous["item_id"]})
    if action.saved.get("business_id") and _id(action.saved["business_id"]) != business:
        raise ProvisioningError("AD_ACCOUNT_CHECKPOINT_MISMATCH", "Saved CREATE belongs to another BM.")
    saved_name = _clean(action.saved.get("account_name") or action.saved.get("name"))
    if saved_name and saved_name.casefold() != name.casefold():
        raise ProvisioningError("AD_ACCOUNT_CHECKPOINT_MISMATCH", "An existing or pending RK operation in this BM belongs to another name; verify that operation first.")
    expected = _id(action.saved.get("ad_account_id") or action.saved.get("create_response_ad_account_id") or state.get("ad_account_id"))
    try:
        before = await _rk_inventory(action.web, business, name, expected)
    except ProvisioningError as exc:
        if getattr(exc, "inventory_diagnostics", None):
            await action.checkpoint({"inventory_verification": exc.inventory_diagnostics,
                "inventory_auth_error": exc.code})
        raise
    await action.checkpoint({"activity": "RK_PRIVATE_INVENTORY_PRECHECK", "business_id": business,
        "inventory_verification": before.get("diagnostics", []), "inventory_complete": before["complete"]})
    pending = _clean(action.saved.get("phase")).upper() in _PENDING
    if before["id"]:
        return await action.commit({"business_id": business, "ad_account_id": "act_" + before["id"],
            "name": name, "currency": currency, "timezone_id": timezone, "verification": before, "recovered": True})
    if expected or pending:
        await action.checkpoint({"phase": "CREATE_RESULT_UNKNOWN", "business_id": business})
        raise ProvisioningError("CREATE_AD_ACCOUNT_RESULT_UNKNOWN", "Previous RK CREATE is retained; exact-BM HTTP inventory is inconclusive. No duplicate POST was sent.", retryable=True)
    if before["ids"] and params.get("allow_multiple_in_business") is not True:
        raise ProvisioningError("AD_ACCOUNT_ALREADY_EXISTS", "Selected BM already contains RK; no additional CREATE was sent.")
    if len(before["named"]) > 1 or not before["complete"]:
        raise ProvisioningError("PRIVATE_RK_INVENTORY_INCONCLUSIVE", "Exact-BM HTTP inventory did not confirm complete absence. No CREATE was sent.", retryable=True)
    await action.checkpoint({"phase": "CREATE_NOT_SUBMITTED", "business_id": business, "account_name": name,
        "baseline_account_ids": before["ids"], "baseline_complete": True, "currency": currency, "timezone_id": timezone})
    contract = rk_create_contract(business_id=business, account_name=name, currency=currency,
        timezone_id=timezone, actor_id=actor)
    await action.checkpoint({"activity": "RK_STATIC_CONTRACT_READY", "capture_doc_id": contract["doc_id"],
        "contract_revision": "meta-settings-2026-10-08"})
    submitted = False
    async def before_submit():
        nonlocal submitted
        await action.checkpoint({"phase": "CREATE_SUBMIT_INTENT", "capture_doc_id": contract["doc_id"], "activity": "RK_PRIVATE_POST_INTENT"})
        submitted = True
    try:
        created = await create_ad_account_with_docids(action.web, business_id=business, account_name=name,
            currency=currency, timezone_id=timezone, profile_id=action.profile, captured_request=contract, before_submit=before_submit)
        expected = _id(created.ad_account_id)
        await action.checkpoint({"phase": "CREATE_RESULT_UNVERIFIED", "create_response_ad_account_id": expected,
            "create_response_path": created.response_path})
    except Exception as exc:
        payload = getattr(exc, "payload", getattr(exc, "meta_payload", {}))
        rejected = isinstance(payload, dict) and bool(payload.get("errors") or payload.get("error")) and not payload.get("data")
        if not submitted or getattr(exc, "request_may_have_been_sent", None) is False or rejected:
            await action.checkpoint({"phase": "CREATE_REJECTED" if rejected else "CREATE_NOT_SUBMITTED"})
            raise ProvisioningError(getattr(exc, "code", "PRIVATE_RK_NOT_SUBMITTED"), str(exc), retryable=bool(getattr(exc, "retryable", True))) from exc
        await action.checkpoint({"phase": "CREATE_RESULT_UNKNOWN"})
    proof = await _verify_rk(action, business, name, expected)
    if proof is None:
        raise ProvisioningError("CREATE_AD_ACCOUNT_RESULT_UNKNOWN", "RK submit is retained; independent exact-BM HTTP inventory has not confirmed it. Retry verifies before any POST.", retryable=True)
    return await action.commit({"business_id": business, "ad_account_id": "act_" + proof["id"],
        "name": name, "currency": currency, "timezone_id": timezone, "verification": proof,
        "create_response_ad_account_id": expected})
