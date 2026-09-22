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
) -> BusinessCreateResult:
    diagnostics: list[dict[str, Any]] = []
    clean_page = str(page_id or "").strip()

    # Route A: official Business Management API.
    # For ReMask we deliberately require an actual Fan Page for this route,
    # matching the page-backed Business creation contract and the user's flow.
    if clean_page and str(getattr(session.context, "access_token", "") or "").strip():
        try:
            graph = await session.graph_api()
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
                    "result": "unknown",
                    "message": str(exc),
                }
            )
            raise BusinessCreateError(
                "CREATE_RESULT_UNKNOWN",
                (
                    "Official create-business request may have reached Meta, "
                    "so ReMask will not retry through another mutation. "
                    "Sync Business Managers before retrying. "
                    + str(exc)
                ),
                retryable=False,
                diagnostics=diagnostics,
            ) from exc
        except GraphApiError as exc:
            diagnostics.append(_graph_diag(exc))
            if not _official_safe_to_web_fallback(exc):
                terminal = _official_terminal_error(exc)
                terminal.diagnostics = diagnostics
                raise terminal from exc

    # Route B: current Facebook Business web flow over the same profile
    # cookies/proxy. This includes the current scope-selector mutation and the
    # legacy Page-backed mutation from the doc_id registry.
    try:
        controller = await session.facebook_controller()
        business_id = await controller.create_business_manager(
            name=business_name,
            page_id=clean_page,
            doc_id=explicit_doc_id,
            user_email=user_email,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            profile_display_name=profile_display_name,
            vertical=vertical,
        )
        return BusinessCreateResult(
            business_id=business_id,
            transport="facebook_web_graphql",
            primary_page_id=clean_page,
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
        raise


__all__ = [
    "BusinessCreateError",
    "BusinessCreateResult",
    "create_business_resilient",
]
