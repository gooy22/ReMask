from __future__ import annotations

import asyncio
import time
from typing import Any

from ..facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser
from .models import ProvisioningError, ProvisioningStep


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _checkpoint_result(step_state: Any) -> dict[str, Any]:
    if not isinstance(step_state, dict):
        return {}
    result = step_state.get("result")
    return dict(result) if isinstance(result, dict) else {}


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

    base_name = _clean(params.get("base_name") or params.get("name") or "ReMask Page")
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
        return _normalize_pages(
            await browser.discover_managed_pages(fast=True)
        )


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
            if rows:
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

    return None, conclusive_absent >= max(1, checks), diagnostics


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
        }
        for row in (checkpoint.get("created_pages") or [])
        if isinstance(row, dict)
        and _clean(row.get("id")).isdigit()
        and _clean(row.get("name"))
    ]
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

        try:
            current_pages = await _fresh_page_inventory(session)
        except BrowserBusinessError as exc:
            if exc.code in {"SESSION_EXPIRED", "CHECKPOINT_REQUIRED", "TWO_FACTOR_REQUIRED"}:
                raise ProvisioningError(exc.code, str(exc), retryable=False) from exc
            current_pages = []

        existing = _find_created_page(
            current_pages,
            page_name=page_name,
            before_ids=set(),
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
                    raise ProvisioningError(
                        exc.code,
                        str(exc),
                        retryable=bool(exc.retryable),
                    ) from exc

                if exc.code != "FAN_PAGE_CREATE_RESULT_UNKNOWN":
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

    if len(ordered) != len(names):
        raise ProvisioningError(
            "FAN_PAGES_INCOMPLETE",
            f"Confirmed {len(ordered)} of {len(names)} requested Fan Pages.",
            retryable=True,
        )

    return {
        "phase": "DONE",
        "requested_count": len(names),
        "created_count": len(ordered),
        "page_ids": [row["id"] for row in ordered],
        "pages": ordered,
        "target_names": names,
        "category": category,
        "transport": "facebook_pages_profile_ui",
    }
