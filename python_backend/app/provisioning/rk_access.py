"""Independent, durable full-control assignment for an existing BM/RK pair."""
from __future__ import annotations

import asyncio
import weakref

from ..private_auth import private_auth_error
from ..private_page_ownership import ensure_private_ad_account_full_control
from .models import ProvisioningError, ProvisioningStep

_LOCKS = weakref.WeakValueDictionary()


async def ensure_existing_rk_full_control(session, *, state, profile, item, scope,
                                         business, account, account_name=""):
    actor = str((getattr(session.context, "cookies", {}) or {}).get("c_user", ""))
    if not actor.isdigit():
        raise ProvisioningError("SESSION_EXPIRED", "The selected profile's HTTP actor is missing.", retryable=True)
    stable = f"workspace-rk-access-facebook:{actor}-{business}-{account}"
    lock = _LOCKS.get(stable)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[stable] = lock
    async with lock:
        retained = (await state.step(stable, ProvisioningStep.PAGE_ACCESS)) or {}
        creator = str(retained.get("profile_id") or profile)
        saved = retained.get("result") or {}
        await state.set_running(stable, creator, stable, ProvisioningStep.PAGE_ACCESS)
        current = ((await state.step(item, ProvisioningStep.PAGE_ACCESS)) or {}).get("result") or {}
        from .advertising_page import AdvertisingPageStore
        from .business_pages import BusinessPageStore
        bundle = await BusinessPageStore(state, session.context, business).get()
        common = await AdvertisingPageStore.for_context(state, session.context, profile).get()
        grant = (bundle.get("grants") or {}).get(business) or (common.get("grants") or {}).get(business) or {}
        current = {**grant, **current}
        prior = {**current, **saved}
        # The current Page checkpoint may hold an older, sent RK operation.
        # Never hide that intent behind the independent checkpoint.
        for key in ("private_target", "private_operations"):
            if key in current:
                prior[key] = current[key]
        async def checkpoint(patch):
            patch = dict(patch)
            if "phase" in patch:
                patch["rk_access_phase"] = patch.pop("phase")
            value = {"business_id": business, "ad_account_id": account, "rk_access_independent": True, **patch}
            await state.checkpoint(stable, creator, stable, ProvisioningStep.PAGE_ACCESS, value)
            await state.checkpoint(item, profile, scope, ProvisioningStep.PAGE_ACCESS, value)
        web = await session.facebook_web()
        web.private_only = True
        try:
            await checkpoint({"activity": "RK_FULL_CONTROL_PRECHECK"})
            from .private_create_handlers import _rk_inventory
            inventory = await _rk_inventory(web, business, account_name, account)
            if inventory.get("id") != account:
                raise ProvisioningError("BUSINESS_RK_RELATION_INCONCLUSIVE",
                    "Fresh HTTP inventory did not confirm the exact BM/RK relation for asset assignment.", retryable=True)
            result = await ensure_private_ad_account_full_control(web, business_id=business,
                ad_account_id=account, profile_id=profile, checkpoint=checkpoint, prior=prior,
                rk_asset_id=inventory.get("asset_ui_id", ""))
        except Exception as exc:
            error = private_auth_error(exc) or exc
            await checkpoint({"diagnostic": {"stage": "rk_full_control", "code": getattr(error, "code", "TASK_FAILED"),
                "verification": getattr(error, "inventory_diagnostics", [])}})
            if error is exc:
                raise
            raise error from exc
        await checkpoint({"activity": "RK_FULL_CONTROL_CONFIRMED", **result})
        return result
