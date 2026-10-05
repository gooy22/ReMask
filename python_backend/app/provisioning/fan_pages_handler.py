from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from ..facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser
from .models import ProvisioningError, ProvisioningStep


def _clean(value: Any) -> str:
    return str(value or "").strip()


_AUTH_RECOVERY_CODES = {
    "CHECKPOINT_REQUIRED",
    "SESSION_EXPIRED",
    "TWO_FACTOR_REQUIRED",
}


def _browser_retryable(exc: BrowserBusinessError) -> bool:
    # These are not safe for blind automatic loops, but they ARE resumable
    # after the operator restores the Facebook session/checkpoint. Marking
    # them retryable lets Retry Failed / the one-click FP flow resume the
    # same idempotent Job instead of forcing a brand new operation.
    return bool(exc.retryable) or exc.code in _AUTH_RECOVERY_CODES


async def _checkpoint_auth_block(
    *,
    provisioning_state: Any,
    item_id: str,
    profile_id: str,
    scope_key: str,
    business_id: str,
    ad_account_id: str,
    exc: BrowserBusinessError,
    target_names: list[str],
    created_pages: list[dict[str, Any]],
    safe_before_submit: bool,
) -> None:
    await provisioning_state.checkpoint(
        item_id,
        profile_id,
        scope_key,
        ProvisioningStep.FAN_PAGES,
        {
            "phase": "PROFILE_AUTH_BLOCKED",
            "resume_from": "CREATE_NEXT" if safe_before_submit else "RECONCILE_CREATE",
            "target_names": target_names,
            "created_pages": created_pages,
            "business_id": _clean(business_id),
            "ad_account_id": _clean(ad_account_id),
            "auth_error_code": exc.code,
            "last_error_code": exc.code,
            "last_error": str(exc)[:4000],
            "browser_diagnostic": (
                exc.diagnostic if isinstance(exc.diagnostic, dict) else {}
            ),
            "safe_before_submit": bool(safe_before_submit),
            "activity": "FAN_PAGE_PROFILE_AUTH_BLOCKED",
            "activity_at": int(time.time()),
        },
    )


def _checkpoint_result(step_state: Any) -> dict[str, Any]:
    if not isinstance(step_state, dict):
        return {}
    result = step_state.get("result")
    return dict(result) if isinstance(result, dict) else {}


async def _confirm_created_pages(session: Any, params: dict[str, Any], checkpoint: dict[str, Any],
                                 pages: list[dict[str, Any]], *, provisioning_state: Any,
                                 item_id: str, profile_id: str, scope_key: str,
                                 target_names: list[str]) -> None:
    from ..facebook_page_confirmation import confirm_main_page
    business = _clean(params.get("main_business_id") or session.context.cookies.get("c_user"))
    if not business.isdigit():
        raise ProvisioningError("MAIN_BUSINESS_REQUIRED", "A numeric main Business scope is required for Page Confirm")
    all_pages = await provisioning_state.latest_profile_fan_pages(profile_id)
    for row in pages:
        if row.get("main_business_confirmed") and _clean(row.get("main_business_id")) == business:
            continue
        page_id = _clean(row.get("id"))
        name = _clean(row.get("name"))
        aliases = {_clean(p.get("id")) for p in [*all_pages, *pages]
                   if _clean(p.get("name")).casefold() == name.casefold()}
        if aliases != {page_id}:
            raise ProvisioningError("PAGE_CONFIRM_AMBIGUOUS", "Multiple saved Pages have this name; Confirm requires an exact Page identity")

        async def save(patch: dict[str, Any]) -> None:
            await provisioning_state.checkpoint(item_id, profile_id, scope_key, ProvisioningStep.FAN_PAGES,
                {"created_pages": pages, "target_names": target_names, **patch})

        verify_only = (_clean(checkpoint.get("phase")) in {"PAGE_CONFIRM_CLICK_INTENT", "PAGE_CONFIRM_RESULT_UNKNOWN"}
                       and _clean(checkpoint.get("confirm_page_id")) == page_id)
        if verify_only:
            await save({"activity":"VERIFY_MAIN_BUSINESS_PAGE"})
        try:
            async with FacebookBusinessBrowser(session.context, timeout_seconds=75, v8_old_space_mb=256) as browser:
                try:
                    result = await asyncio.wait_for(confirm_main_page(browser, business_id=business, page_id=page_id,
                        page_name=name, before_submit=save, verification_only=verify_only), timeout=85)
                except asyncio.TimeoutError as exc:
                    raise BrowserBusinessError("PAGE_CONFIRM_TIMEOUT", "Page Confirm verification exceeded 85s; saved submit intent is preserved", retryable=True) from exc
        except BrowserBusinessError as exc:
            current = _checkpoint_result(await provisioning_state.step(item_id, ProvisioningStep.FAN_PAGES))
            await save({"phase": current.get("phase") or "PAGE_CONFIRM_PENDING",
                        "confirm_page_id": page_id, "main_business_id": business,
                        "last_error_code": exc.code, "browser_diagnostic": exc.diagnostic})
            raise ProvisioningError(exc.code, str(exc) + " diagnostic=" + json.dumps(exc.diagnostic, ensure_ascii=False),
                                    retryable=_browser_retryable(exc)) from exc
        if result.get("confirmed") is not True or _clean(result.get("page_id")) != page_id:
            raise ProvisioningError("PAGE_CONFIRM_RESULT_UNKNOWN", "Meta Page confirmation was not verified", retryable=True)
        row.update(main_business_confirmed=True, main_business_id=business,
                   confirmation_transport="ads_manager_account_overview_ui",
                   confirmation_evidence=result.get('completion_evidence') or {})
        await save({"phase": "PAGE_CONFIRM_CONFIRMED", "confirm_page_id": page_id,
                    "main_business_id": business, "activity": "MAIN_BUSINESS_PAGE_CONFIRMED",
                    "browser_diagnostic":{'stage':'main_page_confirmed','main_business_id':business,
                        'page_id':page_id,'completion_evidence':result.get('completion_evidence') or {}}})


def _target_names(params: dict[str, Any]) -> list[str]:
    raw_names = params.get("names")
    if isinstance(raw_names, list):
        names = [_clean(value) for value in raw_names if _clean(value)]
        if not names:
            raise ProvisioningError(
                "INVALID_INPUT",
                "FAN_PAGES.names must contain at least one Page name",
                retryable=False,
            )
        if len(names) > 10:
            raise ProvisioningError(
                "INVALID_INPUT",
                "FAN_PAGES supports at most 10 Pages per Job",
                retryable=False,
            )
        return names

    base_name = _clean(params.get("base_name") or params.get("name") or "PrgssTeam")
    if not base_name:
        raise ProvisioningError(
            "INVALID_INPUT",
            "FAN_PAGES.base_name is required",
            retryable=False,
        )

    try:
        count = int(params.get("count") or 1)
    except (TypeError, ValueError) as exc:
        raise ProvisioningError(
            "INVALID_INPUT",
            "FAN_PAGES.count must be an integer",
            retryable=False,
        ) from exc

    if count < 1 or count > 10:
        raise ProvisioningError(
            "INVALID_INPUT",
            "FAN_PAGES.count must be between 1 and 10",
            retryable=False,
        )

    if count == 1:
        return [base_name[:120]]
    return [f"{base_name} {index}"[:120] for index in range(1, count + 1)]


def _normalize_pages(rows: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        page_id = _clean(row.get("id"))
        name = _clean(row.get("name"))
        if not page_id.isdigit() or not name:
            continue
        output.append(
            {
                "id": page_id,
                "name": name,
                "business_id": _clean(row.get("business_id")),
            }
        )
    return output


def _find_created_page(
    rows: list[dict[str, Any]],
    *,
    page_name: str,
    before_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    before = before_ids or set()
    matches = [
        row
        for row in rows
        if _clean(row.get("name")).casefold() == _clean(page_name).casefold()
        and _clean(row.get("id")) not in before
        and not _clean(row.get("business_id"))
    ]
    return matches[0] if len(matches) == 1 else None


async def _fresh_page_inventory(session: Any) -> list[dict[str, Any]]:
    async with FacebookBusinessBrowser(
        session.context,
        timeout_seconds=60,
    ) as browser:
        # REMASK_FP_INVENTORY_UNAVAILABLE_IS_NOT_EMPTY_V1
        # discover_managed_pages() raises FAN_PAGES_NOT_DISCOVERED both when
        # the authenticated surface rendered no parseable rows and when the
        # flaky Your-Pages SPA never hydrated. That is NOT authoritative proof
        # of an empty account. Preserve the error so uncertain CREATE recovery
        # can keep duplicate protection enabled.
        rows = await browser.discover_managed_pages(fast=True, navigation_timeout_ms=9000)
        session.context.pages = rows
        return _normalize_pages(rows)


async def _reconcile_uncertain_page(
    session: Any,
    *,
    page_name: str,
    before_ids: set[str],
    checks: int = 3,
) -> tuple[dict[str, Any] | None, bool, list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    conclusive_absent = 0

    for attempt in range(max(1, checks)):
        try:
            rows = await _fresh_page_inventory(session)
            found = _find_created_page(
                rows,
                page_name=page_name,
                before_ids=before_ids,
            )
            diagnostics.append(
                {
                    "attempt": attempt + 1,
                    "result": "ok",
                    "count": len(rows),
                    "ids": [row["id"] for row in rows[:30]],
                }
            )
            if found:
                return found, False, diagnostics
            # Reaching this line means discover_managed_pages() actually
            # returned a parseable inventory. Only such a successful read may
            # count toward an absence proof after an ambiguous CREATE.
            conclusive_absent += 1
        except BrowserBusinessError as exc:
            diagnostics.append(
                {
                    "attempt": attempt + 1,
                    "result": "unavailable",
                    "code": exc.code,
                    "message": str(exc)[:500],
                }
            )
        except Exception as exc:
            diagnostics.append(
                {
                    "attempt": attempt + 1,
                    "result": "unavailable",
                    "code": exc.__class__.__name__,
                    "message": _clean(exc)[:500],
                }
            )

        if attempt < checks - 1:
            await asyncio.sleep(1.5)

    # A parseable list is not a complete inventory: a freshly created Page
    # may still be missing due to hydration or propagation. It cannot authorize
    # a second irreversible CREATE. Positive exact-name evidence can recover.
    return None, False, diagnostics


async def _attach_page_to_business(
    session: Any,
    *,
    provisioning_state: Any,
    item_id: str,
    profile_id: str,
    scope_key: str,
    business_id: str,
    ad_account_id: str,
    page_id: str,
    page_name: str,
    target_names: list[str],
    created_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    business = _clean(business_id)
    page = _clean(page_id)
    ad_account = _clean(ad_account_id)
    if ad_account.lower().startswith("act_"):
        ad_account = ad_account[4:]

    if not business.isdigit() or not page.isdigit():
        raise ProvisioningError(
            "INVALID_INPUT",
            "FAN_PAGES attach requires numeric business_id and page_id.",
            retryable=False,
        )

    step_state = await provisioning_state.step(
        item_id,
        ProvisioningStep.FAN_PAGES,
    )
    checkpoint = _checkpoint_result(step_state)
    phase = _clean(
        checkpoint.get("phase")
        or checkpoint.get("resume_from")
    ).upper()
    checkpoint_business = _clean(
        checkpoint.get("active_attach_business_id")
        or checkpoint.get("business_id")
    )
    checkpoint_page = _clean(
        checkpoint.get("active_attach_page_id")
        or checkpoint.get("page_id")
    )

    # Never blindly resubmit an ownership/add request after a prior final click.
    # First verify whether Meta already attached the Page.
    if (
        phase in {
            "PAGE_ADD_CLICK_INTENT",
            "PAGE_ADD_SUBMITTED",
            "PAGE_ADD_RESULT_UNKNOWN",
        }
        and checkpoint_business == business
        and checkpoint_page == page
    ):
        async with FacebookBusinessBrowser(
            session.context,
            timeout_seconds=60,
        ) as browser:
            attached = await browser.verify_page_attached(
                business_id=business,
                page_id=page,
            )
        if not attached:
            raise ProvisioningError(
                "PAGE_ATTACH_RESULT_UNKNOWN",
                (
                    f"Previous Page attach for {page} -> Business {business} "
                    "may have reached Meta. The Page is not yet visible in "
                    "Business Settings, so ReMask will not submit a duplicate "
                    "attach request."
                ),
                retryable=True,
            )

        return {
            "page_id": page,
            "business_id": business,
            "ad_account_id": ad_account,
            "attached": True,
            "already_attached": True,
            "transport": "facebook_business_settings_page_attach_reconciled",
        }

    async def before_attach(patch: dict[str, Any]) -> None:
        merged = {
            "target_names": target_names,
            "created_pages": created_pages,
            "business_id": business,
            "ad_account_id": ad_account,
            "active_attach_business_id": business,
            "active_attach_page_id": page,
            "active_attach_page_name": page_name,
            "activity_at": int(time.time()),
        }
        if isinstance(patch, dict):
            merged.update(patch)
        await provisioning_state.checkpoint(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.FAN_PAGES,
            merged,
        )

    try:
        async with FacebookBusinessBrowser(
            session.context,
            timeout_seconds=75,
        ) as browser:
            result = await browser.add_existing_page(
                business_id=business,
                page_id=page,
                before_submit=before_attach,
            )
    except BrowserBusinessError as exc:
        if exc.code in _AUTH_RECOVERY_CODES:
            current_state = await provisioning_state.step(
                item_id,
                ProvisioningStep.FAN_PAGES,
            )
            current_phase = _clean(
                _checkpoint_result(current_state).get("phase")
            ).upper()
            await _checkpoint_auth_block(
                provisioning_state=provisioning_state,
                item_id=item_id,
                profile_id=profile_id,
                scope_key=scope_key,
                business_id=business,
                ad_account_id=ad_account,
                exc=exc,
                target_names=target_names,
                created_pages=created_pages,
                safe_before_submit=current_phase not in {
                    "PAGE_ADD_CLICK_INTENT",
                    "PAGE_ADD_SUBMITTED",
                    "PAGE_ADD_RESULT_UNKNOWN",
                },
            )
        if exc.code == "PAGE_ATTACH_RESULT_UNKNOWN":
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.FAN_PAGES,
                {
                    "phase": "PAGE_ADD_RESULT_UNKNOWN",
                    "resume_from": "VERIFY_ATTACH",
                    "target_names": target_names,
                    "created_pages": created_pages,
                    "business_id": business,
                    "ad_account_id": ad_account,
                    "active_attach_business_id": business,
                    "active_attach_page_id": page,
                    "active_attach_page_name": page_name,
                    "last_error_code": exc.code,
                    "last_error": str(exc)[:4000],
                    "browser_diagnostic": (
                        exc.diagnostic
                        if isinstance(exc.diagnostic, dict)
                        else {}
                    ),
                    "activity": "FAN_PAGE_ATTACH_UNCERTAIN",
                    "activity_at": int(time.time()),
                },
            )
        raise ProvisioningError(
            exc.code,
            str(exc),
            retryable=_browser_retryable(exc),
        ) from exc

    return {
        "page_id": page,
        "business_id": business,
        "ad_account_id": ad_account,
        "attached": True,
        "already_attached": bool(
            getattr(result, "already_attached", False)
        ),
        "transport": "facebook_business_settings_page_attach",
    }


async def fan_pages_handler(
    session: Any,
    params: dict[str, Any],
    state: dict[str, Any],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    del args, state

    profile_id = _clean(
        kwargs.get("profile_id")
        or getattr(session.context, "profile_id", "")
    )
    item_id = _clean(kwargs.get("item_id"))
    scope_key = _clean(kwargs.get("scope_key") or "default") or "default"
    provisioning_state = kwargs.get("provisioning_state")

    if not profile_id:
        raise ProvisioningError(
            "INVALID_INPUT",
            "profile_id is required for FAN_PAGES",
            retryable=False,
        )
    if not item_id or provisioning_state is None:
        raise ProvisioningError(
            "INTERNAL_STATE_ERROR",
            "FAN_PAGES requires resumable provisioning state",
            retryable=False,
        )

    business_id = _clean(params.get("business_id"))
    ad_account_id = _clean(params.get("ad_account_id"))
    if ad_account_id.lower().startswith("act_"):
        ad_account_id = ad_account_id[4:]

    existing_page_id = _clean(
        params.get("existing_page_id")
        or params.get("page_id")
    )
    mode = _clean(params.get("mode")).lower()
    if not mode:
        mode = "attach_existing" if existing_page_id else "create"
    if mode not in {"create", "attach_existing", "confirm_existing"}:
        raise ProvisioningError(
            "INVALID_INPUT",
            "FAN_PAGES.mode must be create, attach_existing or confirm_existing.",
            retryable=False,
        )

    if business_id and not business_id.isdigit():
        raise ProvisioningError(
            "INVALID_INPUT",
            "FAN_PAGES.business_id must be numeric.",
            retryable=False,
        )
    if ad_account_id and not ad_account_id.isdigit():
        raise ProvisioningError(
            "INVALID_INPUT",
            "FAN_PAGES.ad_account_id must be numeric.",
            retryable=False,
        )

    if mode in {"attach_existing", "confirm_existing"}:
        if mode == "attach_existing" and not business_id:
            raise ProvisioningError(
                "INVALID_INPUT",
                "attach_existing requires business_id.",
                retryable=False,
            )
        if not existing_page_id.isdigit():
            raise ProvisioningError(
                "INVALID_INPUT",
                "attach_existing requires numeric existing_page_id.",
                retryable=False,
            )
        existing_name = _clean(
            params.get("page_name")
            or params.get("name")
            or f"Page {existing_page_id}"
        )
        names = [existing_name[:120]]
    else:
        names = _target_names(params)

    category = _clean(params.get("category") or "Digital creator")
    bio = _clean(params.get("bio"))

    step_state = kwargs.get("step_state")
    if not isinstance(step_state, dict):
        step_state = await provisioning_state.step(
            item_id,
            ProvisioningStep.FAN_PAGES,
        )
    checkpoint = _checkpoint_result(step_state)

    saved_names = checkpoint.get("target_names")
    if isinstance(saved_names, list):
        normalized_saved = [_clean(value) for value in saved_names]
        if normalized_saved and normalized_saved != names:
            raise ProvisioningError(
                "FAN_PAGES_CHECKPOINT_MISMATCH",
                "Saved FAN_PAGES checkpoint belongs to different Page names.",
                retryable=False,
            )

    created_pages = [
        {
            "id": _clean(row.get("id")),
            "name": _clean(row.get("name")),
            "category": _clean(row.get("category") or category),
            "reused": bool(row.get("reused")),
            "attached": bool(row.get("attached")),
            "business_id": _clean(row.get("business_id")),
            "ad_account_id": _clean(row.get("ad_account_id")),
            "already_attached": bool(row.get("already_attached")),
            "main_business_confirmed": bool(row.get("main_business_confirmed")),
            "main_business_id": _clean(row.get("main_business_id")),
        }
        for row in (checkpoint.get("created_pages") or [])
        if isinstance(row, dict)
        and _clean(row.get("id")).isdigit()
        and _clean(row.get("name"))
    ]
    if mode in {"attach_existing", "confirm_existing"} and not any(
        _clean(row.get("id")) == existing_page_id
        for row in created_pages
    ):
        if mode == "confirm_existing":
            known = await provisioning_state.latest_profile_fan_pages(profile_id)
            exact = [row for row in known if _clean(row.get("id")) == existing_page_id]
            if len(exact) != 1:
                raise ProvisioningError("CONFIRMED_PAGE_REQUIRED", "Confirm requires the saved Page from this FB profile")
            names = [_clean(exact[0].get("name"))]
        created_pages.append(
            {
                "id": existing_page_id,
                "name": names[0],
                "category": category,
                "reused": True,
                "attached": False,
                "business_id": "",
                "ad_account_id": ad_account_id,
                "already_attached": False,
            }
        )

    completed_names = {row["name"].casefold() for row in created_pages}

    prior_phase = _clean(
        checkpoint.get("phase")
        or checkpoint.get("resume_from")
    ).upper()
    active_name = _clean(checkpoint.get("active_page_name"))

    if (
        prior_phase in {"PAGE_CREATE_CLICK_INTENT", "PAGE_CREATE_RESULT_UNKNOWN"}
        and active_name
        and active_name.casefold() not in completed_names
    ):
        before_ids = {
            _clean(value)
            for value in (checkpoint.get("active_before_ids") or [])
            if _clean(value).isdigit()
        }
        found, proven_absent, diagnostics = await _reconcile_uncertain_page(
            session,
            page_name=active_name,
            before_ids=before_ids,
        )
        if found:
            created_pages.append(
                {
                    "id": found["id"],
                    "name": active_name,
                    "category": category,
                    "reused": False,
                    "attached": False,
                    "business_id": business_id,
                    "ad_account_id": ad_account_id,
                    "already_attached": False,
                }
            )
            completed_names.add(active_name.casefold())
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.FAN_PAGES,
                {
                    "phase": "PAGE_CREATED",
                    "resume_from": "CREATE_NEXT",
                    "target_names": names,
                    "created_pages": created_pages,
                    "active_page_name": "",
                    "active_before_ids": [],
                    "reconciliation": diagnostics,
                    "activity": "FAN_PAGE_RECOVERED_AFTER_UNCERTAINTY",
                    "activity_at": int(time.time()),
                },
            )
        elif proven_absent:
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.FAN_PAGES,
                {
                    "phase": "CREATE_NOT_SUBMITTED",
                    "resume_from": "CREATE_NEXT",
                    "target_names": names,
                    "created_pages": created_pages,
                    "active_page_name": "",
                    "active_before_ids": [],
                    "reconciliation": diagnostics,
                    "activity": "FAN_PAGE_UNCERTAINTY_CLEARED",
                    "activity_at": int(time.time()),
                },
            )
        else:
            raise ProvisioningError(
                "FAN_PAGE_CREATE_RESULT_UNKNOWN",
                (
                    f"Previous Create Page for {active_name!r} may have reached "
                    "Facebook. Fresh Page inventory is still inconclusive, so "
                    "ReMask will not risk a duplicate."
                ),
                retryable=True,
            )

    for index, page_name in enumerate(names):
        if page_name.casefold() in completed_names:
            continue

        # Cross-Job exactly-once guard. A Page confirmed by a prior successful
        # FAN_PAGES step is authoritative even while Facebook's rendered
        # "Your Pages" inventory is still propagating. Current live inventory
        # remains the second source, but it must not be the only duplicate
        # protection for mass provisioning.
        persisted_pages: list[dict[str, Any]] = []
        try:
            persisted_pages = await provisioning_state.latest_profile_fan_pages(
                profile_id
            )
        except Exception:
            persisted_pages = []

        persisted_exact = [
            row
            for row in persisted_pages
            if isinstance(row, dict)
            and _clean(row.get("id") or row.get("page_id")).isdigit()
            and _clean(row.get("name")).casefold() == page_name.casefold()
            and (
                not business_id
                or _clean(row.get("business_id")) == business_id
            )
            and (
                not ad_account_id
                or not _clean(row.get("ad_account_id"))
                or _clean(row.get("ad_account_id")) == ad_account_id
            )
        ]
        if len(persisted_exact) == 1:
            persisted_id = _clean(
                persisted_exact[0].get("id")
                or persisted_exact[0].get("page_id")
            )
            created_pages.append(
                {
                    "id": persisted_id,
                    "name": page_name,
                    "category": _clean(
                        persisted_exact[0].get("category") or category
                    ),
                    "reused": True,
                    "attached": bool(persisted_exact[0].get("attached")),
                    "business_id": (
                        business_id
                        or _clean(persisted_exact[0].get("business_id"))
                    ),
                    "ad_account_id": (
                        ad_account_id
                        or _clean(persisted_exact[0].get("ad_account_id"))
                    ),
                    "already_attached": bool(
                        persisted_exact[0].get("already_attached")
                    ),
                }
            )
            completed_names.add(page_name.casefold())
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.FAN_PAGES,
                {
                    "phase": "PAGE_CREATED",
                    "resume_from": "CREATE_NEXT",
                    "target_names": names,
                    "created_pages": created_pages,
                    "activity": "FAN_PAGE_REUSED_FROM_WORKER_STATE",
                    "activity_at": int(time.time()),
                },
            )
            continue

        try:
            current_pages = await _fresh_page_inventory(session)
        except BrowserBusinessError as exc:
            if exc.code in _AUTH_RECOVERY_CODES:
                await _checkpoint_auth_block(
                    provisioning_state=provisioning_state,
                    item_id=item_id,
                    profile_id=profile_id,
                    scope_key=scope_key,
                    business_id=business_id,
                    ad_account_id=ad_account_id,
                    exc=exc,
                    target_names=names,
                    created_pages=created_pages,
                    safe_before_submit=True,
                )
                raise ProvisioningError(
                    exc.code,
                    str(exc),
                    retryable=True,
                ) from exc

            # REMASK_FP_PRECREATE_CONTEXT_BASELINE_V1
            # Browser Page enumeration can be unavailable while the resolver
            # still has saved/current profile Page state. Use that baseline for
            # same-name duplicate avoidance instead of pretending inventory is
            # empty. It is never used as proof after an irreversible submit.
            current_pages = _normalize_pages(
                getattr(session.context, "pages", None) or []
            )

        # For RK-targeted creation, a same-named Page elsewhere on the FB
        # profile is not proof that this RK already owns its intended Page.
        # Reuse by rendered inventory is safe only for the standalone FP flow;
        # targeted retries are recovered through the scoped checkpoint/worker
        # state above.
        existing = (
            None
            if business_id
            else _find_created_page(
                current_pages,
                page_name=page_name,
                before_ids=set(),
            )
        )
        if existing:
            created_pages.append(
                {
                    "id": existing["id"],
                    "name": page_name,
                    "category": category,
                    "reused": True,
                }
            )
            completed_names.add(page_name.casefold())
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.FAN_PAGES,
                {
                    "phase": "PAGE_CREATED",
                    "resume_from": "CREATE_NEXT",
                    "target_names": names,
                    "created_pages": created_pages,
                    "activity": "FAN_PAGE_REUSED",
                    "activity_at": int(time.time()),
                },
            )
            continue

        previous_uncertain = await provisioning_state.latest_uncertain_fan_page(
            profile_id, page_name, exclude_item_id=item_id,
        )
        if previous_uncertain:
            previous_result = previous_uncertain["result"]
            await provisioning_state.checkpoint(
                item_id, profile_id, scope_key, ProvisioningStep.FAN_PAGES,
                {"phase": "PAGE_CREATE_RESULT_UNKNOWN", "target_names": names,
                 "created_pages": created_pages, "active_page_name": page_name,
                 "active_before_ids": previous_result.get("active_before_ids") or [],
                 "recovered_from_item_id": previous_uncertain["item_id"]},
            )
            found, _, diagnostics = await _reconcile_uncertain_page(
                session, page_name=page_name,
                before_ids={_clean(value) for value in previous_result.get("active_before_ids") or []},
            )
            if not found:
                raise ProvisioningError(
                    "PAGE_CREATE_RESULT_UNKNOWN",
                    f"Previous Page CREATE for {page_name!r} remains unconfirmed; another Job must not repeat it.",
                    retryable=True,
                )
            created_pages.append({"id": found["id"], "name": page_name,
                                  "category": category, "reused": True})
            completed_names.add(page_name.casefold())
            await provisioning_state.checkpoint(
                item_id, profile_id, scope_key, ProvisioningStep.FAN_PAGES,
                {"phase": "PAGE_CREATED", "created_pages": created_pages,
                 "active_page_name": "", "reconciliation": diagnostics},
            )
            continue

        create_result: dict[str, Any] | None = None

        for create_attempt in range(1, 3):
            before_ids: set[str] = set()

            async def before_submit(patch: dict[str, Any]) -> None:
                nonlocal before_ids
                before_ids = {
                    _clean(value)
                    for value in (patch.get("before_ids") or [])
                    if _clean(value).isdigit()
                }
                await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.FAN_PAGES,
                    {
                        "phase": "PAGE_CREATE_CLICK_INTENT",
                        "resume_from": "RECONCILE_CREATE",
                        "target_names": names,
                        "created_pages": created_pages,
                        "active_index": index,
                        "active_page_name": page_name,
                        "active_before_ids": sorted(before_ids),
                        "category": category,
                        "create_attempt": create_attempt,
                        "activity": "FAN_PAGE_CREATE_CLICK_INTENT",
                        "activity_at": int(time.time()),
                    },
                )

            try:
                async with FacebookBusinessBrowser(
                    session.context,
                    timeout_seconds=75,
                ) as browser:
                    create_result = await browser.create_fan_page(
                        page_name=page_name,
                        category=category,
                        bio=bio,
                        before_pages=current_pages,
                        before_submit=before_submit,
                    )
                break

            except BrowserBusinessError as exc:
                if exc.code in {
                    "SESSION_EXPIRED",
                    "CHECKPOINT_REQUIRED",
                    "TWO_FACTOR_REQUIRED",
                    "FACEBOOK_TEMPORARILY_BLOCKED",
                    "FAN_PAGE_CREATE_REJECTED",
                }:
                    if exc.code in _AUTH_RECOVERY_CODES:
                        current_state = await provisioning_state.step(
                            item_id,
                            ProvisioningStep.FAN_PAGES,
                        )
                        current_phase = _clean(
                            _checkpoint_result(current_state).get("phase")
                        ).upper()
                        await _checkpoint_auth_block(
                            provisioning_state=provisioning_state,
                            item_id=item_id,
                            profile_id=profile_id,
                            scope_key=scope_key,
                            business_id=business_id,
                            ad_account_id=ad_account_id,
                            exc=exc,
                            target_names=names,
                            created_pages=created_pages,
                            safe_before_submit=current_phase not in {
                                "PAGE_CREATE_CLICK_INTENT",
                                "PAGE_CREATE_RESULT_UNKNOWN",
                            },
                        )
                    raise ProvisioningError(
                        exc.code,
                        str(exc),
                        retryable=_browser_retryable(exc),
                    ) from exc

                if exc.code != "FAN_PAGE_CREATE_RESULT_UNKNOWN":
                    await provisioning_state.checkpoint(item_id,profile_id,scope_key,ProvisioningStep.FAN_PAGES,
                        {'last_error_code':exc.code,'last_error':str(exc)[:2000],
                         'browser_diagnostic':exc.diagnostic or {}})
                    raise ProvisioningError(
                        exc.code,
                        str(exc),
                        retryable=bool(exc.retryable),
                    ) from exc

                await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.FAN_PAGES,
                    {
                        "phase": "PAGE_CREATE_RESULT_UNKNOWN",
                        "resume_from": "RECONCILE_CREATE",
                        "target_names": names,
                        "created_pages": created_pages,
                        "active_index": index,
                        "active_page_name": page_name,
                        "active_before_ids": sorted(before_ids),
                        "category": category,
                        "last_error_code": exc.code,
                        "last_error": str(exc)[:4000],
                        "browser_diagnostic": (
                            exc.diagnostic
                            if isinstance(exc.diagnostic, dict)
                            else {}
                        ),
                        "activity": "FAN_PAGE_RESULT_UNCERTAIN",
                        "activity_at": int(time.time()),
                    },
                )

                found, proven_absent, diagnostics = await _reconcile_uncertain_page(
                    session,
                    page_name=page_name,
                    before_ids=before_ids,
                )
                if found:
                    create_result = {
                        "page_id": found["id"],
                        "name": page_name,
                        "category": category,
                        "reused": False,
                        "transport": "fan_page_uncertain_reconciliation",
                    }
                    break

                if proven_absent and create_attempt < 2:
                    await provisioning_state.checkpoint(
                        item_id,
                        profile_id,
                        scope_key,
                        ProvisioningStep.FAN_PAGES,
                        {
                            "phase": "CREATE_NOT_SUBMITTED",
                            "resume_from": "CREATE_NEXT",
                            "target_names": names,
                            "created_pages": created_pages,
                            "active_page_name": "",
                            "active_before_ids": [],
                            "reconciliation": diagnostics,
                            "activity": "FAN_PAGE_SAFE_RETRY_ALLOWED",
                            "activity_at": int(time.time()),
                        },
                    )
                    continue

                raise ProvisioningError(
                    "FAN_PAGE_CREATE_RESULT_UNKNOWN",
                    (
                        f"Create Page for {page_name!r} has an ambiguous final "
                        "state. ReMask kept duplicate protection enabled."
                    ),
                    retryable=True,
                ) from exc

        if not isinstance(create_result, dict):
            raise ProvisioningError(
                "FAN_PAGE_CREATE_RESULT_UNKNOWN",
                f"Fan Page {page_name!r} did not return a confirmed result.",
                retryable=True,
            )

        page_id = _clean(create_result.get("page_id"))
        if not page_id.isdigit():
            raise ProvisioningError(
                "INVALID_RESULT",
                f"Fan Page {page_name!r} result is missing page_id.",
                retryable=True,
            )

        created_pages.append(
            {
                "id": page_id,
                "name": page_name,
                "category": category,
                "reused": bool(create_result.get("reused")),
                "attached": False,
                "business_id": business_id,
                "ad_account_id": ad_account_id,
                "already_attached": False,
            }
        )
        completed_names.add(page_name.casefold())

        await provisioning_state.checkpoint(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.FAN_PAGES,
            {
                "phase": "PAGE_CREATED",
                "resume_from": "CREATE_NEXT",
                "target_names": names,
                "created_pages": created_pages,
                "active_page_name": "",
                "active_before_ids": [],
                "activity": "FAN_PAGE_CREATED",
                "activity_at": int(time.time()),
            },
        )

    by_name = {row["name"].casefold(): row for row in created_pages}
    ordered = [by_name[name.casefold()] for name in names if name.casefold() in by_name]

    if mode == "confirm_existing" or params.get("confirm_main_business") is True:
        await _confirm_created_pages(session, params, checkpoint, ordered,
            provisioning_state=provisioning_state, item_id=item_id, profile_id=profile_id,
            scope_key=scope_key, target_names=names)

    if business_id:
        for row in ordered:
            row_page_id = _clean(row.get("id"))
            relation_complete = (
                bool(row.get("attached"))
                and _clean(row.get("business_id")) == business_id
                and (
                    not ad_account_id
                    or _clean(row.get("ad_account_id")) == ad_account_id
                )
            )
            if relation_complete:
                continue

            attach_result = await _attach_page_to_business(
                session,
                provisioning_state=provisioning_state,
                item_id=item_id,
                profile_id=profile_id,
                scope_key=scope_key,
                business_id=business_id,
                ad_account_id=ad_account_id,
                page_id=row_page_id,
                page_name=_clean(row.get("name")),
                target_names=names,
                created_pages=created_pages,
            )
            row["attached"] = True
            row["business_id"] = business_id
            row["ad_account_id"] = ad_account_id
            row["already_attached"] = bool(
                attach_result.get("already_attached")
            )
            row["attach_transport"] = _clean(
                attach_result.get("transport")
            )

            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.FAN_PAGES,
                {
                    "phase": "PAGE_ATTACHED",
                    "resume_from": "ATTACH_NEXT",
                    "target_names": names,
                    "created_pages": created_pages,
                    "business_id": business_id,
                    "ad_account_id": ad_account_id,
                    "active_attach_business_id": "",
                    "active_attach_page_id": "",
                    "active_attach_page_name": "",
                    "activity": "FAN_PAGE_ATTACHED_TO_RK_BUSINESS",
                    "activity_at": int(time.time()),
                },
            )

    if len(ordered) != len(names):
        raise ProvisioningError(
            "FAN_PAGES_INCOMPLETE",
            f"Confirmed {len(ordered)} of {len(names)} requested Fan Pages.",
            retryable=True,
        )

    return {
        "phase": "DONE",
        "mode": mode,
        "requested_count": len(names),
        "created_count": (
            0 if mode in {"attach_existing", "confirm_existing"}
            else len(ordered)
        ),
        "attached_count": sum(
            1 for row in ordered if bool(row.get("attached"))
        ),
        "page_ids": [row["id"] for row in ordered],
        "pages": ordered,
        "target_names": names,
        "category": category,
        "main_business_confirmed": bool(ordered) and all(bool(row.get("main_business_confirmed")) for row in ordered),
        "main_business_id": _clean(ordered[0].get("main_business_id")) if ordered else "",
        "business_id": business_id,
        "ad_account_id": ad_account_id,
        "page_business_attached": bool(business_id) and all(bool(row.get("attached")) for row in ordered),
        "ad_account_page_access_verified": False,
        "attachment_scope": "business" if business_id else "profile",
        "transport": (
            "facebook_pages_attach_existing_to_rk"
            if mode == "attach_existing"
            else "facebook_pages_create_and_attach_to_rk"
            if business_id
            else "facebook_pages_profile_ui"
        ),
    }
