from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fb_worker import (
    AuthenticationError,
    RemoteRequestError,
)

from .facebook_graph_api import (
    GraphApiError,
    GraphMutationUncertain,
)


class BusinessCreateError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.diagnostics = diagnostics or []


@dataclass(slots=True)
class BusinessCreateResult:
    business_id: str
    transport: str
    primary_page_id: str = ""
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


def _graph_diag(exc: GraphApiError) -> dict[str, Any]:
    return {
        "transport": "official_graph_api",
        "http_status": exc.http_status,
        "code": exc.code,
        "subcode": exc.subcode,
        "type": exc.error_type,
        "message": str(exc),
    }


def _text(exc: Exception) -> str:
    return str(exc or "").strip().lower()


def _official_page_constraint(exc: GraphApiError) -> bool:
    text = _text(exc)
    if "page" not in text:
        return False
    return any(
        token in text
        for token in (
            "primary page",
            "admin",
            "administrator",
            "owned",
            "owner",
            "full control",
            "permission",
            "eligible",
            "already belongs",
            "another business",
        )
    )


def _official_safe_to_web_fallback(exc: GraphApiError) -> bool:
    """
    Only fall back when the official request was authoritatively rejected
    before creating a Business because the token/app/API route is unavailable.
    """
    text = _text(exc)

    if _official_page_constraint(exc):
        return False

    if exc.code in {190, 10, 200}:
        return True

    if exc.http_status in {401, 403, 404} and any(
        token in text
        for token in (
            "oauth",
            "access token",
            "application",
            "app",
            "permission",
            "unsupported",
            "unknown path",
            "unknown edge",
        )
    ):
        return True

    if exc.code == 100 and any(
        token in text
        for token in (
            "unsupported post request",
            "unknown path",
            "unknown edge",
            "application",
        )
    ):
        return True

    return False


def _find_existing_business(
    businesses: list[dict[str, Any]],
    *,
    business_name: str,
    page_id: str,
) -> str:
    expected_name = str(business_name or "").strip().casefold()
    expected_page = str(page_id or "").strip()

    matches: list[str] = []
    for row in businesses:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get("name") or "").strip().casefold()
        row_page = str(row.get("primary_page_id") or "").strip()
        row_id = str(row.get("id") or "").strip()

        if not row_id or row_name != expected_name:
            continue
        if expected_page and row_page != expected_page:
            continue
        matches.append(row_id)

    return matches[0] if len(matches) == 1 else ""


def _official_terminal_error(exc: GraphApiError) -> BusinessCreateError:
    text = _text(exc)
    diagnostic = _graph_diag(exc)

    if _official_page_constraint(exc):
        return BusinessCreateError(
            "PRIMARY_PAGE_NOT_ELIGIBLE",
            (
                "Meta rejected the selected Fan Page for Business creation: "
                + str(exc)
            ),
            retryable=False,
            diagnostics=[diagnostic],
        )

    if exc.code in {4, 17, 32, 613} or exc.http_status == 429:
        return BusinessCreateError(
            "RATE_LIMITED",
            str(exc),
            retryable=True,
            diagnostics=[diagnostic],
        )

    if exc.http_status in {500, 502, 503, 504}:
        return BusinessCreateError(
            "META_TEMPORARY",
            str(exc),
            retryable=True,
            diagnostics=[diagnostic],
        )

    if any(
        token in text
        for token in (
            "restricted",
            "disabled",
            "policy",
            "not allowed",
            "cannot create",
            "creation limit",
            "maximum",
        )
    ):
        return BusinessCreateError(
            "BUSINESS_CREATION_RESTRICTED",
            str(exc),
            retryable=False,
            diagnostics=[diagnostic],
        )

    return BusinessCreateError(
        "OFFICIAL_CREATE_FAILED",
        str(exc),
        retryable=False,
        diagnostics=[diagnostic],
    )


async def create_business_resilient(
    session: Any,
    *,
    business_name: str,
    page_id: str,
    user_email: str = "",
    user_first_name: str = "",
    user_last_name: str = "",
    profile_display_name: str = "",
    vertical: str = "ADVERTISING",
    timezone_id: int | None = None,
    explicit_doc_id: str | None = None,
    require_page_backed: bool = False,
) -> BusinessCreateResult:
    diagnostics: list[dict[str, Any]] = []
    clean_page = str(page_id or "").strip()

    if require_page_backed and not clean_page:
        raise BusinessCreateError(
            "PRIMARY_PAGE_REQUIRED",
            "Page-backed Business creation requires a selected Fan Page.",
            retryable=False,
            diagnostics=diagnostics,
        )

    # Route A: official Business Management API.
    #
    # Do not attempt the mutation merely because a Page ID was supplied.
    # First prove that this exact token can see the selected Page. That avoids
    # misleading official calls when Pages are visible only to the browser
    # session but not to the saved Ads/Graph token.
    if clean_page and str(getattr(session.context, "access_token", "") or "").strip():
        graph = await session.graph_api()
        official_page_visible = False
        official_graph_blocked = False
        saved_page_visible = any(
            str(page.get("id") or "").strip() == clean_page
            for page in (getattr(session.context, "pages", None) or [])
            if isinstance(page, dict)
        )

        try:
            visible_pages = await graph.list_pages()
            official_page_visible = any(
                str(page.get("id") or "").strip() == clean_page
                for page in visible_pages
                if isinstance(page, dict)
            )
            diagnostics.append(
                {
                    "transport": "official_graph_api",
                    "stage": "page_visibility",
                    "page_id": clean_page,
                    "visible": official_page_visible,
                    "saved_profile_visible": saved_page_visible,
                    "pages_count": len(visible_pages),
                }
            )
        except GraphApiError as exc:
            diagnostics.append(
                {
                    **_graph_diag(exc),
                    "stage": "page_visibility",
                }
            )
            if exc.code == 190 or exc.http_status == 401:
                official_graph_blocked = True
                diagnostics.append(
                    {
                        "transport": "official_graph_api",
                        "stage": "route_selection",
                        "result": "skip_official_create",
                        "reason": "saved access token is not usable for Graph API",
                    }
                )

        if (official_page_visible or saved_page_visible) and not official_graph_blocked:
            if saved_page_visible and not official_page_visible:
                diagnostics.append(
                    {
                        "transport": "official_graph_api",
                        "stage": "page_visibility",
                        "page_id": clean_page,
                        "result": "using_saved_profile_page_reference",
                    }
                )

            official_permissions: dict[str, str] = {}
            try:
                official_permissions = await graph.list_permissions()
                diagnostics.append(
                    {
                        "transport": "official_graph_api",
                        "stage": "permissions",
                        "business_management": official_permissions.get(
                            "business_management",
                            "",
                        ),
                        "pages_show_list": official_permissions.get(
                            "pages_show_list",
                            "",
                        ),
                        "ads_management": official_permissions.get(
                            "ads_management",
                            "",
                        ),
                    }
                )
            except GraphApiError as exc:
                diagnostics.append(
                    {
                        **_graph_diag(exc),
                        "stage": "permissions",
                    }
                )

            business_management_status = official_permissions.get(
                "business_management"
            )
            official_create_allowed = (
                business_management_status in (None, "", "granted")
            )

            if not official_create_allowed:
                diagnostics.append(
                    {
                        "transport": "official_graph_api",
                        "stage": "create",
                        "result": "skipped",
                        "reason": (
                            "business_management permission is not granted"
                        ),
                    }
                )
            else:
                try:
                    existing = await graph.list_businesses()
                    existing_id = _find_existing_business(
                        existing,
                        business_name=business_name,
                        page_id=clean_page,
                    )
                    if existing_id:
                        diagnostics.append(
                            {
                                "transport": "official_graph_api",
                                "stage": "precreate_dedup",
                                "result": "existing_business_reused",
                                "business_id": existing_id,
                            }
                        )
                        return BusinessCreateResult(
                            business_id=existing_id,
                            transport="official_graph_api_reused",
                            primary_page_id=clean_page,
                            diagnostics=diagnostics,
                        )
                except GraphApiError as exc:
                    diagnostics.append(
                        {
                            **_graph_diag(exc),
                            "stage": "precreate_dedup",
                        }
                    )

                try:
                    business_id = await graph.create_business(
                        name=business_name,
                        primary_page_id=clean_page,
                        vertical=vertical,
                        email=user_email,
                        timezone_id=timezone_id,
                    )
                    return BusinessCreateResult(
                        business_id=business_id,
                        transport="official_graph_api",
                        primary_page_id=clean_page,
                        diagnostics=diagnostics,
                    )
                except GraphMutationUncertain as exc:
                    diagnostics.append(
                        {
                            "transport": "official_graph_api",
                            "stage": "create",
                            "result": "unknown",
                            "message": str(exc),
                        }
                    )

                    # The POST may already have succeeded. Verify before doing
                    # anything else; never fall through to a second mutation.
                    try:
                        existing = await graph.list_businesses()
                        existing_id = _find_existing_business(
                            existing,
                            business_name=business_name,
                            page_id=clean_page,
                        )
                        if existing_id:
                            diagnostics.append(
                                {
                                    "transport": "official_graph_api",
                                    "stage": "verify_after_unknown",
                                    "result": "created_business_found",
                                    "business_id": existing_id,
                                }
                            )
                            return BusinessCreateResult(
                                business_id=existing_id,
                                transport="official_graph_api_verified",
                                primary_page_id=clean_page,
                                diagnostics=diagnostics,
                            )
                    except GraphApiError as verify_exc:
                        diagnostics.append(
                            {
                                **_graph_diag(verify_exc),
                                "stage": "verify_after_unknown",
                            }
                        )

                    raise BusinessCreateError(
                        "CREATE_RESULT_UNKNOWN",
                        (
                            "Official create-business request may have reached Meta, "
                            "but ReMask could not verify the result. Sync Business "
                            "Managers before retrying; automatic fallback is blocked "
                            "to prevent duplicate BMs."
                        ),
                        retryable=False,
                        diagnostics=diagnostics,
                    ) from exc
                except GraphApiError as exc:
                    diagnostics.append(
                        {
                            **_graph_diag(exc),
                            "stage": "create",
                        }
                    )
                    if not _official_safe_to_web_fallback(exc):
                        terminal = _official_terminal_error(exc)
                        terminal.diagnostics = diagnostics
                        raise terminal from exc

    # Route B: current Facebook Business web flow over the same profile
    # cookies/proxy. This includes the current scope-selector mutation and the
    # legacy Page-backed mutation from the doc_id registry.
    try:
        controller = await session.facebook_controller()
        web_result = await controller.create_business_manager_detailed(
            name=business_name,
            page_id=clean_page,
            doc_id=explicit_doc_id,
            user_email=user_email,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            profile_display_name=profile_display_name,
            vertical=vertical,
            allow_scope_selector_fallback=not require_page_backed,
        )

        page_was_in_mutation = (
            web_result.candidate.variables_mode == "legacy_primary_page_v1"
            and bool(clean_page)
        )

        if require_page_backed and not page_was_in_mutation:
            raise BusinessCreateError(
                "PAGE_BACKED_BM_ROUTE_UNAVAILABLE",
                (
                    "Facebook Business was not created because the available "
                    "web mutation does not carry primary_page_id. ReMask "
                    "refuses to report success without the selected Fan Page."
                ),
                retryable=False,
                diagnostics=diagnostics,
            )

        transport = (
            "facebook_web_graphql_page_backed"
            if page_was_in_mutation
            else "facebook_web_graphql_scope_selector"
        )
        diagnostics.append(
            {
                "transport": transport,
                "stage": "create",
                "doc_id": web_result.candidate.doc_id,
                "friendly_name": web_result.candidate.friendly_name,
                "variables_mode": web_result.candidate.variables_mode,
                "source": web_result.candidate.source,
                "response_path": web_result.response_path,
                "selected_page_id": clean_page,
                "primary_page_sent_in_mutation": page_was_in_mutation,
            }
        )

        return BusinessCreateResult(
            business_id=web_result.business_id,
            transport=transport,
            primary_page_id=clean_page if page_was_in_mutation else "",
            diagnostics=diagnostics,
        )
    except AuthenticationError as exc:
        diagnostics.append(
            {
                "transport": "facebook_web_graphql",
                "code": "SESSION_EXPIRED",
                "message": str(exc),
            }
        )
        raise BusinessCreateError(
            "SESSION_EXPIRED",
            str(exc),
            retryable=False,
            diagnostics=diagnostics,
        ) from exc
    except RemoteRequestError as exc:
        diagnostics.append(
            {
                "transport": "facebook_web_graphql",
                "http_status": exc.http_status,
                "message": str(exc),
                "payload": exc.meta_payload,
            }
        )

        text = _text(exc)
        result_may_be_unknown = (
            not exc.meta_payload
            and any(
                token in text
                for token in (
                    "timeout",
                    "network failure",
                    "connection",
                    "non-json",
                    "empty response",
                )
            )
        )

        if (
            require_page_backed
            and (
                "no usable page-backed create_bm mutation" in text
                or "refusing non-page fallback" in text
            )
        ):
            raise BusinessCreateError(
                "PAGE_BACKED_BM_ROUTE_UNAVAILABLE",
                (
                    "Meta's current Page-backed Business creation mutation "
                    "could not be resolved for this profile. The selected Fan "
                    "Page was not ignored and no scope-selector BM was created."
                ),
                retryable=False,
                diagnostics=diagnostics,
            ) from exc

        if (
            not str(user_email or "").strip()
            and "requires user_email" in text
        ):
            raise BusinessCreateError(
                "BUSINESS_EMAIL_REQUIRED",
                (
                    "The official Page-backed route is unavailable for this "
                    "profile and the current Facebook web creation mutation "
                    "requires a Business email. Provide BUSINESS.user_email "
                    "and start a new Add BM job."
                ),
                retryable=False,
                diagnostics=diagnostics,
            ) from exc

        if result_may_be_unknown:
            # A private mutation may already have been committed by Meta.
            # If the official read-side is available, use it only to verify the
            # outcome. Never issue a second CREATE automatically.
            try:
                if str(getattr(session.context, "access_token", "") or "").strip():
                    graph = await session.graph_api()
                    existing = await graph.list_businesses()
                    existing_id = _find_existing_business(
                        existing,
                        business_name=business_name,
                        page_id=clean_page,
                    )
                    if existing_id:
                        diagnostics.append(
                            {
                                "transport": "facebook_web_graphql",
                                "stage": "verify_after_unknown",
                                "result": "created_business_found",
                                "business_id": existing_id,
                            }
                        )
                        return BusinessCreateResult(
                            business_id=existing_id,
                            transport="facebook_web_graphql_verified",
                            primary_page_id=clean_page,
                            diagnostics=diagnostics,
                        )
            except GraphApiError as verify_exc:
                diagnostics.append(
                    {
                        **_graph_diag(verify_exc),
                        "stage": "verify_web_after_unknown",
                    }
                )

            raise BusinessCreateError(
                "CREATE_RESULT_UNKNOWN",
                (
                    "Private Facebook Business mutation may have succeeded, but "
                    "its response was lost. ReMask will not send another CREATE "
                    "until Business Managers are synced, preventing duplicates."
                ),
                retryable=False,
                diagnostics=diagnostics,
            ) from exc

        raise


__all__ = [
    "BusinessCreateError",
    "BusinessCreateResult",
    "create_business_resilient",
]
