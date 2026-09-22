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

from .facebook_business_create import (
    DocIdMutationError,
    set_business_primary_page,
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


async def _create_business_via_web(
    session: Any,
    *,
    business_name: str,
    clean_page: str,
    user_email: str,
    user_first_name: str,
    user_last_name: str,
    profile_display_name: str,
    vertical: str,
    explicit_doc_id: str | None,
    require_page_backed: bool,
    diagnostics: list[dict[str, Any]],
) -> BusinessCreateResult:
    """
    Create a Business through the authenticated Facebook browser session.

    This is the primary Add BM route: the same profile cookies/proxy are used
    to obtain actor_id + fb_dtsg and execute the current persisted mutation
    selected from the runtime doc_id registry.
    """
    try:
        controller = await session.facebook_controller()
        if require_page_backed and not str(user_email or "").strip():
            raise BusinessCreateError(
                "BUSINESS_EMAIL_REQUIRED",
                (
                    "The current private Business creation flow requires an "
                    "email before the selected Fan Page can be attached."
                ),
                retryable=False,
                diagnostics=diagnostics,
            )

        web_result = await controller.create_business_manager_detailed(
            name=business_name,
            page_id=clean_page,
            doc_id=explicit_doc_id,
            user_email=user_email,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            profile_display_name=profile_display_name,
            vertical=vertical,
            allow_scope_selector_fallback=True,
        )

        page_was_in_mutation = (
            web_result.candidate.variables_mode == "legacy_primary_page_v1"
            and bool(clean_page)
        )

        primary_page_attached_after_create = False

        if require_page_backed and not page_was_in_mutation:
            try:
                attach_candidate = await set_business_primary_page(
                    controller.session,
                    business_id=web_result.business_id,
                    business_name=business_name,
                    page_id=clean_page,
                )
                primary_page_attached_after_create = True
                diagnostics.append(
                    {
                        "transport": "facebook_web_graphql",
                        "stage": "set_primary_page",
                        "result": "success",
                        "business_id": web_result.business_id,
                        "page_id": clean_page,
                        "doc_id": attach_candidate.doc_id,
                        "friendly_name": attach_candidate.friendly_name,
                        "source": attach_candidate.source,
                    }
                )
            except DocIdMutationError as exc:
                diagnostics.append(
                    {
                        "transport": "facebook_web_graphql",
                        "stage": "set_primary_page",
                        "result": "failed_after_business_created",
                        "business_id": web_result.business_id,
                        "page_id": clean_page,
                        "message": str(exc),
                        "payload": exc.payload,
                    }
                )
                raise BusinessCreateError(
                    "BUSINESS_CREATED_PAGE_ATTACH_FAILED",
                    (
                        f"Business {web_result.business_id} was created, but "
                        "the selected Fan Page could not be set as primary. "
                        "Do not repeat CREATE automatically; sync Business "
                        "Managers first. Page attach error: "
                        + str(exc)
                    ),
                    retryable=False,
                    diagnostics=diagnostics,
                ) from exc

        transport = (
            "facebook_web_graphql_page_backed"
            if page_was_in_mutation
            else (
                "facebook_web_graphql_scope_selector_plus_primary_page"
                if primary_page_attached_after_create
                else "facebook_web_graphql_scope_selector"
            )
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
            primary_page_id=(
                clean_page
                if page_was_in_mutation or primary_page_attached_after_create
                else ""
            ),
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
                    "The current Facebook Page-backed CREATE_BM mutation could "
                    "not be resolved. No official CREATE was sent and the "
                    "selected Fan Page was not ignored."
                ),
                retryable=False,
                diagnostics=diagnostics,
            ) from exc

        if any(
            token in text
            for token in (
                "no usable create_bm doc_id candidate",
                "no create_bm doc_id candidates",
                "no current page-backed create_bm mutation",
                "no current create_bm doc_id was found",
                "no usable create_bm candidate completed successfully",
                "no usable set_primary_page mutation",
            )
        ):
            raise BusinessCreateError(
                "CREATE_BM_MUTATION_NOT_DISCOVERED",
                (
                    "ReMask could not resolve the current Facebook private "
                    "Business mutation from this profile session."
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
                    "The resolved Facebook web mutation requires a Business "
                    "email. Provide BUSINESS.user_email and start a new Add BM job."
                ),
                retryable=False,
                diagnostics=diagnostics,
            ) from exc

        if result_may_be_unknown:
            # The private POST may already have reached Meta. Use the official
            # API only as a read-side verification if it is available; never
            # issue another CREATE automatically.
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
                    "its response was lost. ReMask will not send a second CREATE "
                    "until Business Managers are synced."
                ),
                retryable=False,
                diagnostics=diagnostics,
            ) from exc

        raise


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

    # ReMask Add BM is a browser-session workflow. When a Fan Page is selected,
    # use the private Facebook web mutation FIRST: cookies/proxy -> actor_id ->
    # fb_dtsg -> current Page-backed doc_id -> CREATE_BM. Do not send an
    # official Graph CREATE before this path.
    if clean_page:
        diagnostics.append(
            {
                "transport": "facebook_web_graphql",
                "stage": "route_selection",
                "result": "primary",
                "reason": "selected Fan Page uses private create + primary Page attach flow",
            }
        )
        return await _create_business_via_web(
            session,
            business_name=business_name,
            clean_page=clean_page,
            user_email=user_email,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            profile_display_name=profile_display_name,
            vertical=vertical,
            explicit_doc_id=explicit_doc_id,
            require_page_backed=require_page_backed,
            diagnostics=diagnostics,
        )

    # No Fan Page was supplied. Keep the official route only for legacy callers
    # that explicitly opt out of Page-backed Add BM.
    if str(getattr(session.context, "access_token", "") or "").strip():
        graph = await session.graph_api()
        try:
            official_permissions = await graph.list_permissions()
        except GraphApiError as exc:
            diagnostics.append(
                {
                    **_graph_diag(exc),
                    "stage": "permissions",
                }
            )
            official_permissions = {}

        if official_permissions.get("business_management") == "granted":
            # The official API implementation itself requires a primary Page,
            # so without one there is nothing safe to submit here.
            diagnostics.append(
                {
                    "transport": "official_graph_api",
                    "stage": "create",
                    "result": "skipped",
                    "reason": "official create requires a primary Page ID",
                }
            )

    # Final legacy fallback is the scope-selector private mutation. This is not
    # used by normal Add BM because that flow always selects a Fan Page.
    return await _create_business_via_web(
        session,
        business_name=business_name,
        clean_page="",
        user_email=user_email,
        user_first_name=user_first_name,
        user_last_name=user_last_name,
        profile_display_name=profile_display_name,
        vertical=vertical,
        explicit_doc_id=explicit_doc_id,
        require_page_backed=False,
        diagnostics=diagnostics,
    )


__all__ = [
    "BusinessCreateError",
    "BusinessCreateResult",
    "create_business_resilient",
]
