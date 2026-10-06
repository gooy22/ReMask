from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .advertising_page import AdvertisingPageStore
from .models import ProvisioningError
from .service import ProvisioningService
from .state import ProvisioningStateStore


@dataclass(slots=True, frozen=True)
class DesiredProfileState:
    ad_accounts: int = 2
    require_page: bool = True
    require_business: bool = True
    require_page_access: bool = True
    require_payment: bool = True
    preferred_business_id: str = ""


class PrepareService:
    """Bring one profile toward a declared infrastructure state.

    Prepare never reimplements Meta mutations. It plans what is missing and
    delegates each mutation to the existing ProvisioningService so current
    checkpoints, reconciliation and duplicate protection remain authoritative.
    """

    def __init__(
        self,
        state: ProvisioningStateStore,
        provisioning: ProvisioningService,
    ) -> None:
        self.state = state
        self.provisioning = provisioning

    @staticmethod
    def _desired(payload: dict[str, Any]) -> DesiredProfileState:
        raw = payload.get("desired") or {}
        if not isinstance(raw, dict):
            raise ProvisioningError(
                "PREPARE_INVALID_DESIRED_STATE",
                "prepare.desired must be an object",
                retryable=False,
            )

        try:
            ad_accounts = int(raw.get("ad_accounts", 2))
        except (TypeError, ValueError) as exc:
            raise ProvisioningError(
                "PREPARE_INVALID_AD_ACCOUNT_COUNT",
                "desired.ad_accounts must be an integer",
                retryable=False,
            ) from exc
        if not 1 <= ad_accounts <= 20:
            raise ProvisioningError(
                "PREPARE_INVALID_AD_ACCOUNT_COUNT",
                "desired.ad_accounts must be from 1 to 20",
                retryable=False,
            )

        return DesiredProfileState(
            ad_accounts=ad_accounts,
            require_page=raw.get("fan_page", True) is not False,
            require_business=raw.get("business", True) is not False,
            require_page_access=raw.get("page_access", True) is not False,
            require_payment=raw.get("payment", True) is not False,
            preferred_business_id=str(raw.get("business_id") or "").strip(),
        )

    @staticmethod
    def _parameters(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = payload.get("parameters") or {}
        if not isinstance(raw, dict):
            raise ProvisioningError(
                "PREPARE_INVALID_PARAMETERS",
                "prepare.parameters must be an object",
                retryable=False,
            )
        result: dict[str, dict[str, Any]] = {}
        for name in ("FAN_PAGES", "BUSINESS", "AD_ACCOUNT", "PAGE_ACCESS"):
            value = raw.get(name, raw.get(name.lower(), {}))
            if value is None:
                value = {}
            if not isinstance(value, dict):
                raise ProvisioningError(
                    "PREPARE_INVALID_PARAMETERS",
                    f"parameters.{name} must be an object",
                    retryable=False,
                )
            result[name] = deepcopy(value)
        return result

    @staticmethod
    def _stable_token(profile_id: str, scope_key: str) -> str:
        return hashlib.sha256(
            f"{profile_id}:{scope_key}".encode("utf-8")
        ).hexdigest()[:12]

    @staticmethod
    def _trace(
        trace: list[dict[str, Any]],
        action: str,
        phase: str,
        **extra: Any,
    ) -> None:
        trace.append({"action": action, "phase": phase, **extra})

    async def _business_inventory(self, profile_id: str) -> list[dict[str, Any]]:
        groups = await self.state.confirmed_business_binding_groups()
        businesses = ((groups.get(profile_id) or {}).get("businesses") or {})
        rows = [
            dict(value)
            for value in businesses.values()
            if isinstance(value, dict)
            and str(value.get("business_id") or "").isdigit()
        ]
        rows.sort(key=lambda row: int(row.get("updated_at") or 0), reverse=True)
        return rows

    async def _choose_business(
        self,
        profile_id: str,
        preferred: str,
    ) -> dict[str, Any] | None:
        rows = await self._business_inventory(profile_id)
        if preferred:
            return next(
                (
                    row
                    for row in rows
                    if str(row.get("business_id") or "") == preferred
                ),
                None,
            )
        return rows[0] if rows else None

    async def _run_provisioning(
        self,
        *,
        child_item_id: str,
        profile_id: str,
        context: Any,
        session: Any,
        scope_key: str,
        steps: list[str],
        parameters: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self.provisioning.run(
            item_id=child_item_id,
            profile_id=profile_id,
            context=context,
            session=session,
            payload={
                "scope_key": scope_key,
                "steps": steps,
                "parameters": parameters,
            },
            task_idempotency_key=idempotency_key,
        )

    async def run(
        self,
        *,
        item_id: str,
        profile_id: str,
        context: Any,
        session: Any,
        payload: dict[str, Any],
        task_idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProvisioningError(
                "PREPARE_INVALID_INPUT",
                "prepare payload must be an object",
                retryable=False,
            )

        desired = self._desired(payload)
        parameters = self._parameters(payload)
        base_scope = str(
            payload.get("scope_key")
            or task_idempotency_key
            or f"prepare:{profile_id}"
        ).strip() or f"prepare:{profile_id}"
        trace: list[dict[str, Any]] = []

        page_store = AdvertisingPageStore.for_context(
            self.state,
            context,
            profile_id,
        )
        page = await page_store.get()
        page_id = str(page.get("page_id") or "").strip()

        if desired.require_page and not page_id.isdigit():
            self._trace(trace, "FAN_PAGES", "PRECHECK", status="MISSING")
            page_params = {
                "common_page": True,
                "mode": "create",
                "count": 1,
                "confirm_main_business": False,
                **parameters["FAN_PAGES"],
            }
            self._trace(trace, "FAN_PAGES", "EXECUTE")
            await self._run_provisioning(
                child_item_id=f"{item_id}:prepare:page",
                profile_id=profile_id,
                context=context,
                session=session,
                scope_key=f"{base_scope}:page",
                steps=["FAN_PAGES"],
                parameters={"FAN_PAGES": page_params},
                idempotency_key=f"{base_scope}:page",
            )
            page = await page_store.get()
            page_id = str(page.get("page_id") or "").strip()
            self._trace(
                trace,
                "FAN_PAGES",
                "VERIFY",
                status="CONFIRMED" if page_id.isdigit() else "INCONCLUSIVE",
                page_id=page_id,
            )
            if not page_id.isdigit():
                raise ProvisioningError(
                    "PREPARE_PAGE_UNCONFIRMED",
                    "Prepare could not confirm the advertising Page",
                    retryable=True,
                )
            self._trace(trace, "FAN_PAGES", "COMMIT", page_id=page_id)
        elif desired.require_page:
            self._trace(
                trace,
                "FAN_PAGES",
                "PRECHECK",
                status="CONFIRMED",
                page_id=page_id,
            )

        business = await self._choose_business(
            profile_id,
            desired.preferred_business_id,
        )
        if desired.preferred_business_id and business is None:
            self._trace(
                trace,
                "BUSINESS",
                "PRECHECK",
                status="PREFERRED_MISSING",
                business_id=desired.preferred_business_id,
            )

        if desired.require_business and business is None:
            self._trace(trace, "BUSINESS", "PRECHECK", status="MISSING")
            token = self._stable_token(profile_id, base_scope)
            business_params = {
                "name": f"ReMask {profile_id} {token[:6]}",
                "user_email": f"{token}@gmail.com",
                "use_created_page": False,
                "attach_page": False,
                **parameters["BUSINESS"],
            }
            self._trace(trace, "BUSINESS", "EXECUTE")
            result = await self._run_provisioning(
                child_item_id=f"{item_id}:prepare:business",
                profile_id=profile_id,
                context=context,
                session=session,
                scope_key=f"{base_scope}:business",
                steps=["BUSINESS"],
                parameters={"BUSINESS": business_params},
                idempotency_key=f"{base_scope}:business",
            )
            business_id = str(
                ((result.get("state") or {}).get("business_id") or "")
            ).strip()
            business = await self._choose_business(profile_id, business_id)
            self._trace(
                trace,
                "BUSINESS",
                "VERIFY",
                status="CONFIRMED" if business else "INCONCLUSIVE",
                business_id=business_id,
            )
            if business is None:
                raise ProvisioningError(
                    "PREPARE_BUSINESS_UNCONFIRMED",
                    "Prepare could not confirm the Business after creation",
                    retryable=True,
                )
            self._trace(
                trace,
                "BUSINESS",
                "COMMIT",
                business_id=str(business.get("business_id") or ""),
            )
        elif desired.require_business and business is not None:
            self._trace(
                trace,
                "BUSINESS",
                "PRECHECK",
                status="CONFIRMED",
                business_id=str(business.get("business_id") or ""),
            )

        business_id = str((business or {}).get("business_id") or "").strip()
        if desired.require_business and not business_id.isdigit():
            raise ProvisioningError(
                "PREPARE_BUSINESS_REQUIRED",
                "Prepare requires one confirmed Business",
                retryable=True,
            )

        existing_accounts = (
            await self.state.confirmed_ad_accounts_for_profile(
                profile_id,
                business_id,
            )
            if business_id
            else []
        )
        self._trace(
            trace,
            "AD_ACCOUNT",
            "PRECHECK",
            status="CONFIRMED" if len(existing_accounts) >= desired.ad_accounts else "MISSING",
            confirmed=len(existing_accounts),
            desired=desired.ad_accounts,
            business_id=business_id,
        )

        base_rk = parameters["AD_ACCOUNT"]
        for slot in range(len(existing_accounts) + 1, desired.ad_accounts + 1):
            rk_params = deepcopy(base_rk)
            rk_params["business_id"] = business_id
            rk_params.pop("bm_id", None)
            rk_params.pop("ad_account_id", None)
            rk_params.setdefault("name", f"ReMask {profile_id} RK {slot}")
            rk_params.setdefault("use_common_page", desired.require_page_access)

            if not str(rk_params.get("currency") or "").strip():
                raise ProvisioningError(
                    "PREPARE_RK_CURRENCY_REQUIRED",
                    "parameters.AD_ACCOUNT.currency is required",
                    retryable=False,
                )
            if rk_params.get("timezone_id") in (None, ""):
                raise ProvisioningError(
                    "PREPARE_RK_TIMEZONE_REQUIRED",
                    "parameters.AD_ACCOUNT.timezone_id is required",
                    retryable=False,
                )

            child = f"{item_id}:prepare:rk:{slot}"
            scope = f"{base_scope}:rk:{slot}"
            self._trace(
                trace,
                "AD_ACCOUNT",
                "EXECUTE",
                slot=slot,
                business_id=business_id,
            )
            result = await self._run_provisioning(
                child_item_id=child,
                profile_id=profile_id,
                context=context,
                session=session,
                scope_key=scope,
                steps=["AD_ACCOUNT"],
                parameters={
                    "AD_ACCOUNT": rk_params,
                    "PAGE_ACCESS": {
                        **parameters["PAGE_ACCESS"],
                        "business_id": business_id,
                    },
                },
                idempotency_key=scope,
            )
            ad_account_id = str(
                ((result.get("state") or {}).get("ad_account_id") or "")
            ).removeprefix("act_").strip()
            current = await self.state.confirmed_ad_accounts_for_profile(
                profile_id,
                business_id,
            )
            confirmed = any(
                str(row.get("ad_account_id") or "") == ad_account_id
                for row in current
            )
            self._trace(
                trace,
                "AD_ACCOUNT",
                "VERIFY",
                slot=slot,
                status="CONFIRMED" if confirmed else "INCONCLUSIVE",
                ad_account_id=ad_account_id,
            )
            if not confirmed:
                raise ProvisioningError(
                    "PREPARE_RK_UNCONFIRMED",
                    f"Prepare could not confirm RK slot {slot}",
                    retryable=True,
                )
            self._trace(
                trace,
                "AD_ACCOUNT",
                "COMMIT",
                slot=slot,
                ad_account_id=ad_account_id,
            )
            existing_accounts = current

        accounts = await self.state.confirmed_ad_accounts_for_profile(
            profile_id,
            business_id,
        )
        accounts = accounts[: desired.ad_accounts]

        readiness: list[dict[str, Any]] = []
        access_ready = True
        payment_ready = True
        for row in accounts:
            account_id = str(row.get("ad_account_id") or "")
            access = (
                await self.state.page_access_confirmed(
                    profile_id,
                    business_id,
                    account_id,
                )
                if desired.require_page_access
                else True
            )
            payment = (
                await self.state.funding_confirmed(profile_id, account_id)
                if desired.require_payment
                else True
            )
            access_ready = access_ready and access
            payment_ready = payment_ready and payment
            readiness.append(
                {
                    **row,
                    "page_access_confirmed": access,
                    "payment_confirmed": payment,
                }
            )

        object_ready = (
            (not desired.require_page or page_id.isdigit())
            and (not desired.require_business or business_id.isdigit())
            and len(accounts) >= desired.ad_accounts
        )
        ready_to_launch = (
            object_ready
            and access_ready
            and payment_ready
        )

        if ready_to_launch:
            status = "READY_TO_LAUNCH"
            reason = ""
        elif not object_ready:
            status = "RETRY"
            reason = "INFRASTRUCTURE_INCOMPLETE"
        elif desired.require_page_access and not access_ready:
            status = "MANUAL_REQUIRED"
            reason = "PAGE_ACCESS_UNCONFIRMED"
        elif desired.require_payment and not payment_ready:
            status = "PAYMENT_REQUIRED"
            reason = "PAYMENT_UNCONFIRMED"
        else:
            status = "PREPARED"
            reason = ""

        return {
            "profile_id": profile_id,
            "scope_key": base_scope,
            "status": status,
            "reason": reason,
            "desired": {
                "fan_page": desired.require_page,
                "business": desired.require_business,
                "ad_accounts": desired.ad_accounts,
                "page_access": desired.require_page_access,
                "payment": desired.require_payment,
            },
            "actual": {
                "page_id": page_id,
                "business_id": business_id,
                "ad_accounts": readiness,
            },
            "ready_to_launch": ready_to_launch,
            "trace": trace,
        }
