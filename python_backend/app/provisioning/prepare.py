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

    async def _business_inventory(
        self,
        profile_id: str,
        context: Any = None,
    ) -> list[dict[str, Any]]:
        groups = await self.state.confirmed_business_binding_groups()
        businesses = ((groups.get(profile_id) or {}).get("businesses") or {})

        merged: dict[str, dict[str, Any]] = {}
        for value in businesses.values():
            if not isinstance(value, dict):
                continue
            business_id = str(value.get("business_id") or "").strip()
            if not business_id.isdigit():
                continue
            merged[business_id] = dict(value)

        live_updated_at = int(
            getattr(context, "inventory_updated_at", 0) or 0
        ) if context is not None else 0
        for value in (getattr(context, "businesses", None) or []):
            if not isinstance(value, dict):
                continue
            business_id = str(
                value.get("business_id") or value.get("id") or ""
            ).strip()
            if not business_id.isdigit():
                continue
            previous = merged.get(business_id) or {}
            merged[business_id] = {
                **previous,
                "business_id": business_id,
                "business_name": str(
                    value.get("business_name")
                    or value.get("name")
                    or previous.get("business_name")
                    or business_id
                ).strip(),
                "source": str(
                    value.get("source")
                    or "workspace_last_confirmed_live"
                ).strip(),
                "updated_at": max(
                    int(previous.get("updated_at") or 0),
                    live_updated_at,
                ),
                "live_inventory_confirmed": True,
            }

        rows = list(merged.values())
        rows.sort(
            key=lambda row: (
                bool(row.get("live_inventory_confirmed")),
                int(row.get("updated_at") or 0),
            ),
            reverse=True,
        )
        return rows

    async def _choose_business(
        self,
        profile_id: str,
        preferred: str,
        context: Any = None,
    ) -> dict[str, Any] | None:
        rows = await self._business_inventory(profile_id, context)
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

    async def _ad_account_inventory(
        self,
        profile_id: str,
        business_id: str,
        context: Any = None,
    ) -> list[dict[str, Any]]:
        durable = await self.state.confirmed_ad_accounts_for_profile(
            profile_id,
            business_id,
        )
        merged: dict[str, dict[str, Any]] = {
            str(row.get("ad_account_id") or ""): dict(row)
            for row in durable
            if isinstance(row, dict)
            and str(row.get("ad_account_id") or "").isdigit()
        }

        live_updated_at = int(
            getattr(context, "inventory_updated_at", 0) or 0
        ) if context is not None else 0
        for value in (getattr(context, "ad_accounts", None) or []):
            if not isinstance(value, dict):
                continue
            bound_business = str(value.get("business_id") or "").strip()
            account_id = str(
                value.get("ad_account_id")
                or value.get("account_id")
                or value.get("id")
                or ""
            ).removeprefix("act_").strip()
            if bound_business != business_id or not account_id.isdigit():
                continue
            previous = merged.get(account_id) or {}
            merged[account_id] = {
                **previous,
                "business_id": business_id,
                "ad_account_id": account_id,
                "account_name": str(
                    value.get("account_name")
                    or value.get("name")
                    or previous.get("account_name")
                    or account_id
                ).strip(),
                "scope_key": str(previous.get("scope_key") or ""),
                "updated_at": max(
                    int(previous.get("updated_at") or 0),
                    live_updated_at,
                ),
                "source": str(
                    value.get("source")
                    or "workspace_last_confirmed_live"
                ).strip(),
                "live_inventory_confirmed": True,
            }

        rows = list(merged.values())
        rows.sort(
            key=lambda row: (
                bool(row.get("live_inventory_confirmed")),
                int(row.get("updated_at") or 0),
                str(row.get("ad_account_id") or ""),
            ),
            reverse=True,
        )
        return rows

    async def _bundle_inventory(
        self,
        profile_id: str,
        context: Any = None,
    ) -> list[dict[str, Any]]:
        """Return Business containers with their confirmed RK inventory.

        Prepare treats one Business as one infrastructure slot. If a legacy BM
        already contains multiple RK, only one can satisfy the slot; the extras
        are preserved but never counted as additional desired bundles.
        """
        rows: list[dict[str, Any]] = []
        for business in await self._business_inventory(profile_id, context):
            business_id = str(business.get("business_id") or "").strip()
            if not business_id.isdigit():
                continue
            accounts = await self._ad_account_inventory(
                profile_id,
                business_id,
                context,
            )
            rows.append(
                {
                    "business_id": business_id,
                    "business": business,
                    "ad_accounts": accounts,
                }
            )
        return rows

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

        base_rk = parameters["AD_ACCOUNT"]
        if not str(base_rk.get("currency") or "").strip():
            raise ProvisioningError(
                "PREPARE_RK_CURRENCY_REQUIRED",
                "parameters.AD_ACCOUNT.currency is required",
                retryable=False,
            )
        if base_rk.get("timezone_id") in (None, ""):
            raise ProvisioningError(
                "PREPARE_RK_TIMEZONE_REQUIRED",
                "parameters.AD_ACCOUNT.timezone_id is required",
                retryable=False,
            )

        # One infrastructure slot is exactly one Business + one RK.
        # REMASK_PREPARE_ONE_RK_PER_BUSINESS_V1
        inventory = await self._bundle_inventory(profile_id, context)
        preferred = desired.preferred_business_id
        preferred_bundle = next(
            (
                row
                for row in inventory
                if str(row.get("business_id") or "") == preferred
            ),
            None,
        ) if preferred else None
        if preferred and preferred_bundle is None:
            self._trace(
                trace,
                "BUSINESS",
                "PRECHECK",
                status="PREFERRED_MISSING",
                business_id=preferred,
            )

        ready_bundles = [
            row for row in inventory if row.get("ad_accounts")
        ]
        empty_bundles = [
            row for row in inventory if not row.get("ad_accounts")
        ]
        ordered = ready_bundles + empty_bundles
        if preferred_bundle is not None:
            ordered = [
                preferred_bundle,
                *[
                    row
                    for row in ordered
                    if row is not preferred_bundle
                ],
            ]

        self._trace(
            trace,
            "BUSINESS",
            "PRECHECK",
            status=(
                "CONFIRMED"
                if len(inventory) >= desired.ad_accounts
                else "MISSING"
            ),
            confirmed=len(inventory),
            desired=desired.ad_accounts,
            topology="ONE_RK_PER_BUSINESS",
        )
        self._trace(
            trace,
            "AD_ACCOUNT",
            "PRECHECK",
            status=(
                "CONFIRMED"
                if len(ready_bundles) >= desired.ad_accounts
                else "MISSING"
            ),
            confirmed_businesses_with_rk=len(ready_bundles),
            desired=desired.ad_accounts,
            topology="ONE_RK_PER_BUSINESS",
        )

        selected: list[dict[str, Any]] = []
        extras: list[dict[str, Any]] = []

        for slot in range(1, desired.ad_accounts + 1):
            candidate = ordered[slot - 1] if slot <= len(ordered) else None

            if candidate is None:
                if not desired.require_business:
                    break

                token = self._stable_token(
                    profile_id,
                    f"{base_scope}:bundle:{slot}",
                )
                business_params = {
                    "name": f"ReMask {profile_id} BM {slot} {token[:4]}",
                    "user_email": f"{token}@gmail.com",
                    "use_created_page": False,
                    "attach_page": False,
                    **parameters["BUSINESS"],
                }
                business_scope = f"{base_scope}:bundle:{slot}:business"
                self._trace(
                    trace,
                    "BUSINESS",
                    "EXECUTE",
                    slot=slot,
                )
                result = await self._run_provisioning(
                    child_item_id=f"{item_id}:prepare:bundle:{slot}:business",
                    profile_id=profile_id,
                    context=context,
                    session=session,
                    scope_key=business_scope,
                    steps=["BUSINESS"],
                    parameters={"BUSINESS": business_params},
                    idempotency_key=business_scope,
                )
                business_id = str(
                    ((result.get("state") or {}).get("business_id") or "")
                ).strip()
                business = await self._choose_business(
                    profile_id,
                    business_id,
                    context,
                )
                self._trace(
                    trace,
                    "BUSINESS",
                    "VERIFY",
                    slot=slot,
                    status=(
                        "CONFIRMED"
                        if business is not None
                        else "INCONCLUSIVE"
                    ),
                    business_id=business_id,
                )
                if business is None or not business_id.isdigit():
                    raise ProvisioningError(
                        "PREPARE_BUSINESS_UNCONFIRMED",
                        f"Prepare could not confirm Business slot {slot}",
                        retryable=True,
                    )
                self._trace(
                    trace,
                    "BUSINESS",
                    "COMMIT",
                    slot=slot,
                    business_id=business_id,
                )
                candidate = {
                    "business_id": business_id,
                    "business": business,
                    "ad_accounts": [],
                }

            business_id = str(candidate.get("business_id") or "").strip()
            if not business_id.isdigit():
                raise ProvisioningError(
                    "PREPARE_BUSINESS_REQUIRED",
                    f"Prepare bundle {slot} has no confirmed Business",
                    retryable=True,
                )

            accounts = [
                row
                for row in (candidate.get("ad_accounts") or [])
                if isinstance(row, dict)
                and str(row.get("ad_account_id") or "").isdigit()
            ]
            if len(accounts) > 1:
                extras.extend(
                    {
                        **row,
                        "business_id": business_id,
                        "ignored_by_topology": True,
                    }
                    for row in accounts[1:]
                )
                self._trace(
                    trace,
                    "AD_ACCOUNT",
                    "PRECHECK",
                    slot=slot,
                    status="EXTRA_EXISTING_RK_IGNORED",
                    business_id=business_id,
                    extras=len(accounts) - 1,
                )

            account = accounts[0] if accounts else None
            if account is None:
                rk_params = deepcopy(base_rk)
                rk_params["business_id"] = business_id
                rk_params.pop("allow_multiple_in_business", None)
                rk_params.pop("bm_id", None)
                rk_params.pop("ad_account_id", None)
                rk_params.setdefault(
                    "name",
                    f"ReMask {profile_id} RK {slot}",
                )
                rk_params.setdefault(
                    "use_common_page",
                    desired.require_page_access,
                )

                rk_scope = f"{base_scope}:bundle:{slot}:rk"
                self._trace(
                    trace,
                    "AD_ACCOUNT",
                    "EXECUTE",
                    slot=slot,
                    business_id=business_id,
                )
                result = await self._run_provisioning(
                    child_item_id=f"{item_id}:prepare:bundle:{slot}:rk",
                    profile_id=profile_id,
                    context=context,
                    session=session,
                    scope_key=rk_scope,
                    steps=["AD_ACCOUNT"],
                    parameters={
                        "AD_ACCOUNT": rk_params,
                        "PAGE_ACCESS": {
                            **parameters["PAGE_ACCESS"],
                            "business_id": business_id,
                        },
                    },
                    idempotency_key=rk_scope,
                )
                ad_account_id = str(
                    ((result.get("state") or {}).get("ad_account_id") or "")
                ).removeprefix("act_").strip()
                current = await self._ad_account_inventory(
                    profile_id,
                    business_id,
                    context,
                )
                if ad_account_id.isdigit():
                    account = next(
                        (
                            row
                            for row in current
                            if str(row.get("ad_account_id") or "")
                            == ad_account_id
                        ),
                        None,
                    )
                elif len(current) == 1:
                    account = current[0]
                    ad_account_id = str(
                        account.get("ad_account_id") or ""
                    ).strip()

                self._trace(
                    trace,
                    "AD_ACCOUNT",
                    "VERIFY",
                    slot=slot,
                    status=(
                        "CONFIRMED"
                        if account is not None
                        else "INCONCLUSIVE"
                    ),
                    business_id=business_id,
                    ad_account_id=ad_account_id,
                )
                if account is None:
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
                    business_id=business_id,
                    ad_account_id=ad_account_id,
                )
            else:
                self._trace(
                    trace,
                    "AD_ACCOUNT",
                    "PRECHECK",
                    slot=slot,
                    status="CONFIRMED",
                    business_id=business_id,
                    ad_account_id=str(
                        account.get("ad_account_id") or ""
                    ),
                )

            selected.append(
                {
                    "slot": slot,
                    "business_id": business_id,
                    "business": candidate.get("business") or {},
                    "ad_account": account,
                }
            )

        if desired.require_page_access:
            for bundle in selected:
                business_id = str(bundle["business_id"])
                row = bundle["ad_account"]
                account_id = str(row.get("ad_account_id") or "").strip()
                access_confirmed = await self.state.page_access_confirmed(
                    profile_id,
                    business_id,
                    account_id,
                )
                if access_confirmed:
                    self._trace(
                        trace,
                        "PAGE_ACCESS",
                        "PRECHECK",
                        slot=bundle["slot"],
                        status="CONFIRMED",
                        business_id=business_id,
                        ad_account_id=account_id,
                    )
                    continue

                self._trace(
                    trace,
                    "PAGE_ACCESS",
                    "PRECHECK",
                    slot=bundle["slot"],
                    status="MISSING",
                    business_id=business_id,
                    ad_account_id=account_id,
                )
                access_scope = (
                    f"{base_scope}:bundle:{bundle['slot']}:access:{account_id}"
                )
                access_params = {
                    **parameters["PAGE_ACCESS"],
                    "existing_target": True,
                    "business_id": business_id,
                    "ad_account_id": account_id,
                    "ad_account_name": str(
                        row.get("account_name") or ""
                    ).strip(),
                    "policies_accepted": True,
                }
                self._trace(
                    trace,
                    "PAGE_ACCESS",
                    "EXECUTE",
                    slot=bundle["slot"],
                    business_id=business_id,
                    ad_account_id=account_id,
                )
                await self._run_provisioning(
                    child_item_id=(
                        f"{item_id}:prepare:bundle:{bundle['slot']}:"
                        f"access:{account_id}"
                    ),
                    profile_id=profile_id,
                    context=context,
                    session=session,
                    scope_key=access_scope,
                    steps=["PAGE_ACCESS"],
                    parameters={"PAGE_ACCESS": access_params},
                    idempotency_key=access_scope,
                )
                access_confirmed = await self.state.page_access_confirmed(
                    profile_id,
                    business_id,
                    account_id,
                )
                self._trace(
                    trace,
                    "PAGE_ACCESS",
                    "VERIFY",
                    slot=bundle["slot"],
                    status=(
                        "CONFIRMED"
                        if access_confirmed
                        else "INCONCLUSIVE"
                    ),
                    business_id=business_id,
                    ad_account_id=account_id,
                )
                if not access_confirmed:
                    raise ProvisioningError(
                        "PREPARE_PAGE_ACCESS_UNCONFIRMED",
                        (
                            "Prepare could not confirm Page access for RK "
                            f"{account_id}"
                        ),
                        retryable=True,
                    )
                self._trace(
                    trace,
                    "PAGE_ACCESS",
                    "COMMIT",
                    slot=bundle["slot"],
                    business_id=business_id,
                    ad_account_id=account_id,
                )

        readiness: list[dict[str, Any]] = []
        bundle_rows: list[dict[str, Any]] = []
        access_ready = True
        payment_ready = True

        for bundle in selected:
            business_id = str(bundle["business_id"])
            row = bundle["ad_account"]
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

            account_row = {
                **row,
                "business_id": business_id,
                "slot": bundle["slot"],
                "page_access_confirmed": access,
                "payment_confirmed": payment,
            }
            readiness.append(account_row)
            bundle_rows.append(
                {
                    "slot": bundle["slot"],
                    "business_id": business_id,
                    "business_name": str(
                        (bundle.get("business") or {}).get("business_name")
                        or (bundle.get("business") or {}).get("name")
                        or ""
                    ).strip(),
                    "ad_account_id": account_id,
                    "ad_account_name": str(
                        row.get("account_name") or ""
                    ).strip(),
                    "page_access_confirmed": access,
                    "payment_confirmed": payment,
                }
            )

        object_ready = (
            (not desired.require_page or page_id.isdigit())
            and (
                not desired.require_business
                or len(selected) >= desired.ad_accounts
            )
            and len(readiness) >= desired.ad_accounts
            and len(
                {
                    str(row.get("business_id") or "")
                    for row in readiness
                    if str(row.get("business_id") or "").isdigit()
                }
            )
            >= desired.ad_accounts
        )
        ready_to_launch = object_ready and access_ready and payment_ready

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

        business_ids = [
            str(row.get("business_id") or "")
            for row in bundle_rows
            if str(row.get("business_id") or "").isdigit()
        ]

        return {
            "profile_id": profile_id,
            "scope_key": base_scope,
            "status": status,
            "reason": reason,
            "desired": {
                "fan_page": desired.require_page,
                "business": desired.require_business,
                "ad_accounts": desired.ad_accounts,
                "businesses": desired.ad_accounts,
                "topology": "ONE_RK_PER_BUSINESS",
                "page_access": desired.require_page_access,
                "payment": desired.require_payment,
            },
            "actual": {
                "page_id": page_id,
                # Legacy single-value field retained for UI compatibility.
                "business_id": business_ids[0] if business_ids else "",
                "business_ids": business_ids,
                "bundles": bundle_rows,
                "ad_accounts": readiness,
                "extra_ad_accounts": extras,
            },
            "ready_to_launch": ready_to_launch,
            "trace": trace,
        }

