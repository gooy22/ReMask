# python_backend/app/facebook_business_create.py

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .facebook_docids import (
    DocIdCandidate,
    classify_cache_failure,
    list_candidates,
    record_result,
    upsert_candidate,
)
from .facebook_query_discovery import (
    discover_persisted_query,
)


CREATE_BM_OPERATION = "CREATE_BM"
SET_PRIMARY_PAGE_OPERATION = "SET_PRIMARY_PAGE"

CREATE_BM_FRIENDLY_NAME = (
    "useBusinessCreationMutationMutation"
)

SET_PRIMARY_PAGE_FRIENDLY_NAME = (
    "BizKitSettingsUpdateBusinessBasicInfoMutation"
)

BUSINESS_GRAPHQL_URL = (
    "https://business.facebook.com/api/graphql/"
)


class BusinessMutationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        payload: dict[str, Any] | None = None,
        candidate: DocIdCandidate | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(
            code
            or "BUSINESS_MUTATION_FAILED"
        )
        self.retryable = bool(
            retryable
        )
        self.payload = (
            payload
            if isinstance(
                payload,
                dict,
            )
            else {}
        )
        self.candidate = candidate


class DocIdMutationError(
    BusinessMutationError
):
    pass


@dataclass(slots=True)
class CreateBusinessResult:
    business_id: str
    candidate: DocIdCandidate
    response: dict[str, Any]
    response_path: str


@dataclass(slots=True)
class AttachPageResult:
    business_id: str
    page_id: str
    candidate: DocIdCandidate
    response: dict[str, Any]


def _clean(
    value: Any,
) -> str:
    return str(
        value
        or ""
    ).strip()


def _profile_id(
    session: Any,
    explicit: str = "",
) -> str:
    value = _clean(
        explicit
    )

    if value:
        return value

    return (
        _clean(
            getattr(
                getattr(
                    session,
                    "profile",
                    None,
                ),
                "name",
                "",
            )
        )
        or "<unknown-profile>"
    )


def _derive_name_parts(
    *,
    display_name: str,
    first_name: str,
    last_name: str,
) -> tuple[str, str]:
    first = _clean(
        first_name
    )
    last = _clean(
        last_name
    )

    if first and last:
        return first, last

    parts = [
        part
        for part in re.split(
            r"\s+",
            _clean(
                display_name
            ),
        )
        if part
    ]

    if (
        not first
        and parts
    ):
        first = parts[0]

    if not last:
        if len(
            parts
        ) >= 2:
            last = parts[-1]
        else:
            last = "Business"

    return (
        first or "Business",
        last or "Owner",
    )


def candidate_requirements(
    candidate: DocIdCandidate,
) -> dict[str, bool]:
    if (
        candidate.variables_mode
        == "scope_selector_business_creation_v1"
    ):
        return {
            "email": True,
            "page_id": False,
        }

    if (
        candidate.variables_mode
        == "bizkit_settings_update_business_basic_info_v1"
    ):
        return {
            "email": False,
            "page_id": True,
        }

    return {
        "email": False,
        "page_id": False,
    }


def _graphql_errors(
    payload: dict[str, Any],
) -> list[Any]:
    raw = payload.get(
        "errors"
    )

    if isinstance(
        raw,
        list,
    ):
        return raw

    if raw:
        return [
            raw
        ]

    raw_error = payload.get(
        "error"
    )

    if raw_error:
        return [
            raw_error
        ]

    return []


def _diagnostic(
    candidate: DocIdCandidate,
    payload: dict[str, Any] | None,
    message: str = "",
) -> str:
    body = ""

    if payload:
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except Exception:
            body = repr(
                payload
            )

    return (
        f"doc_id={candidate.doc_id} "
        f"friendly_name={candidate.friendly_name or '-'} "
        f"variables_mode={candidate.variables_mode or '-'} "
        f"source={candidate.source or '-'} "
        f"message={message or '-'} "
        f"payload={body[:5000]}"
    )


def _extract_create_business_id(
    payload: dict[str, Any],
) -> tuple[str, str]:
    data = payload.get(
        "data"
    )

    if not isinstance(
        data,
        dict,
    ):
        return "", ""

    node = data.get(
        "bizkit_create_business"
    )

    if not isinstance(
        node,
        dict,
    ):
        return "", ""

    direct = _clean(
        node.get(
            "id"
        )
    )

    if direct.isdigit():
        return (
            direct,
            "data.bizkit_create_business.id",
        )

    business = node.get(
        "business"
    )

    if isinstance(
        business,
        dict,
    ):
        nested = _clean(
            business.get(
                "id"
            )
        )

        if nested.isdigit():
            return (
                nested,
                "data.bizkit_create_business.business.id",
            )

    return "", ""


def _build_create_variables(
    *,
    actor_id: str,
    business_name: str,
    user_email: str,
    user_first_name: str,
    user_last_name: str,
    profile_display_name: str,
) -> dict[str, Any]:
    actor = _clean(
        actor_id
    )

    name = _clean(
        business_name
    )

    email = _clean(
        user_email
    )

    if not actor:
        raise BusinessMutationError(
            "FB_ACTOR_ID_MISSING",
            "Facebook actor_id is missing",
            retryable=False,
        )

    if not name:
        raise BusinessMutationError(
            "INVALID_INPUT",
            "Business name is missing",
            retryable=False,
        )

    if not email:
        raise BusinessMutationError(
            "BUSINESS_EMAIL_REQUIRED",
            "Business email is required",
            retryable=False,
        )

    first_name, last_name = (
        _derive_name_parts(
            display_name=(
                profile_display_name
            ),
            first_name=(
                user_first_name
            ),
            last_name=(
                user_last_name
            ),
        )
    )

    return {
        "input": {
            "client_mutation_id": (
                uuid.uuid4().hex[:16]
            ),
            "actor_id": actor,
            "business_name": name,
            "user_first_name": first_name,
            "user_last_name": last_name,
            "user_email": email,
            "creation_source": (
                "MBS_BUSINESS_CREATION_IN_SCOPE_SELECTOR"
            ),
            "entry_point": (
                "UNIFIED_GLOBAL_SCOPE_SELECTOR"
            ),
        }
    }


def _build_attach_variables(
    *,
    actor_id: str,
    business_id: str,
    business_name: str,
    page_id: str,
) -> dict[str, Any]:
    actor = _clean(
        actor_id
    )

    business = _clean(
        business_id
    )

    page = _clean(
        page_id
    )

    name = _clean(
        business_name
    )

    if not actor:
        raise BusinessMutationError(
            "FB_ACTOR_ID_MISSING",
            "Facebook actor_id is missing",
            retryable=False,
        )

    if not business.isdigit():
        raise BusinessMutationError(
            "INVALID_BUSINESS_ID",
            "business_id must be numeric",
            retryable=False,
        )

    if not page.isdigit():
        raise BusinessMutationError(
            "INVALID_PRIMARY_PAGE",
            "page_id must be numeric",
            retryable=False,
        )

    return {
        "input": {
            "client_mutation_id": (
                uuid.uuid4().hex[:16]
            ),
            "actor_id": actor,
            "business_id": business,
            "business_name": name,
            "primary_page_id": page,
            "entry_point": (
                "BUSINESS_MANAGER_BUSINESS_INFO"
            ),
        }
    }


async def discover_current_scope_selector_create_candidate(
    session: Any,
) -> DocIdCandidate | None:
    discovered = await discover_persisted_query(
        session,
        friendly_name=(
            CREATE_BM_FRIENDLY_NAME
        ),
        entry_urls=[
            "https://www.facebook.com/",
            "https://business.facebook.com/latest/home",
        ],
        max_scripts_per_entry=0,
        cache_ttl_seconds=0,
    )

    if discovered is None:
        return None

    return DocIdCandidate(
        operation=(
            CREATE_BM_OPERATION
        ),
        doc_id=(
            discovered.doc_id
        ),
        friendly_name=(
            CREATE_BM_FRIENDLY_NAME
        ),
        endpoint_url=(
            BUSINESS_GRAPHQL_URL
        ),
        variables_mode=(
            "scope_selector_business_creation_v1"
        ),
        source=(
            "dynamic_html"
            if discovered.source_kind
            == "html"
            else "dynamic_response_headers"
        ),
        priority=20_000,
        observed_at=str(
            int(
                time.time()
            )
        ),
        enabled=True,
    )


async def discover_current_set_primary_page_candidate(
    session: Any,
    *,
    business_id: str,
) -> DocIdCandidate | None:
    clean_business_id = _clean(
        business_id
    )

    if not clean_business_id.isdigit():
        return None

    discovered = await discover_persisted_query(
        session,
        friendly_name=(
            SET_PRIMARY_PAGE_FRIENDLY_NAME
        ),
        entry_urls=[
            (
                "https://business.facebook.com/latest/settings/"
                f"business_info?business_id={clean_business_id}"
            ),
            "https://business.facebook.com/latest/home",
        ],
        max_scripts_per_entry=0,
        cache_ttl_seconds=0,
    )

    if discovered is None:
        return None

    return DocIdCandidate(
        operation=(
            SET_PRIMARY_PAGE_OPERATION
        ),
        doc_id=(
            discovered.doc_id
        ),
        friendly_name=(
            SET_PRIMARY_PAGE_FRIENDLY_NAME
        ),
        endpoint_url=(
            BUSINESS_GRAPHQL_URL
        ),
        variables_mode=(
            "bizkit_settings_update_business_basic_info_v1"
        ),
        source=(
            "dynamic_html"
            if discovered.source_kind
            == "html"
            else "dynamic_response_headers"
        ),
        priority=20_000,
        observed_at=str(
            int(
                time.time()
            )
        ),
        enabled=True,
    )


def _unique_candidates(
    candidates: list[DocIdCandidate],
) -> list[DocIdCandidate]:
    output: list[
        DocIdCandidate
    ] = []

    seen: set[
        tuple[
            str,
            str,
            str,
            str,
        ]
    ] = set()

    for candidate in candidates:
        key = (
            candidate.doc_id,
            candidate.friendly_name,
            candidate.variables_mode,
            candidate.endpoint_url,
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        output.append(
            candidate
        )

    return output


async def create_business_with_docids(
    session: Any,
    *,
    business_name: str,
    user_email: str,
    user_first_name: str = "",
    user_last_name: str = "",
    profile_display_name: str = "",
    manual_doc_id: str = "",
    explicit_doc_id: str | None = None,
    profile_id: str = "",
    page_id: str = "",
    vertical: str = "ADVERTISING",
    allow_scope_selector_fallback: bool = True,
) -> CreateBusinessResult:
    del page_id
    del vertical
    del allow_scope_selector_fallback

    clean_profile_id = _profile_id(
        session,
        profile_id,
    )

    clean_manual_doc_id = _clean(
        manual_doc_id
        or explicit_doc_id
    )

    if (
        clean_manual_doc_id
        and not re.fullmatch(
            r"\d{5,40}",
            clean_manual_doc_id,
        )
    ):
        raise BusinessMutationError(
            "INVALID_INPUT",
            "manual_doc_id must contain 5-40 digits",
            retryable=False,
        )

    bootstrap = await session.bootstrap()

    actor_id = _clean(
        getattr(
            bootstrap,
            "actor_id",
            "",
        )
    )

    variables = _build_create_variables(
        actor_id=actor_id,
        business_name=business_name,
        user_email=user_email,
        user_first_name=user_first_name,
        user_last_name=user_last_name,
        profile_display_name=profile_display_name,
    )

    dynamic_candidate: (
        DocIdCandidate
        | None
    )

    try:
        dynamic_candidate = (
            await asyncio.wait_for(
                discover_current_scope_selector_create_candidate(
                    session
                ),
                timeout=20.0,
            )
        )
    except Exception:
        dynamic_candidate = None

    ordered: list[
        DocIdCandidate
    ] = []

    if dynamic_candidate is not None:
        ordered.append(
            dynamic_candidate
        )

    elif clean_manual_doc_id:
        ordered.append(
            DocIdCandidate(
                operation=(
                    CREATE_BM_OPERATION
                ),
                doc_id=(
                    clean_manual_doc_id
                ),
                friendly_name=(
                    CREATE_BM_FRIENDLY_NAME
                ),
                endpoint_url=(
                    BUSINESS_GRAPHQL_URL
                ),
                variables_mode=(
                    "scope_selector_business_creation_v1"
                ),
                source="job_manual",
                priority=19_000,
                observed_at="runtime",
                enabled=True,
            )
        )

    confirmed_cache = list_candidates(
        CREATE_BM_OPERATION,
        confirmed_only=True,
    )

    ordered.extend(
        confirmed_cache
    )

    candidates = _unique_candidates(
        ordered
    )

    if not candidates:
        raise BusinessMutationError(
            "CREATE_BM_MUTATION_NOT_DISCOVERED",
            (
                "No current CREATE_BM mutation was discovered in initial "
                "Facebook HTML/response headers, no manual_doc_id was available, "
                "and no previously confirmed cache candidate exists. "
                "No CREATE request was sent."
            ),
            retryable=False,
        )

    diagnostics: list[str] = []

    for candidate in candidates:
        try:
            response = await session.graphql(
                candidate.doc_id,
                variables,
                friendly_name=(
                    candidate.friendly_name
                ),
                endpoint_url=(
                    candidate.endpoint_url
                ),
            )

        except Exception as exc:
            payload = getattr(
                exc,
                "meta_payload",
                None,
            )

            if not isinstance(
                payload,
                dict,
            ):
                payload = {}

            http_status = getattr(
                exc,
                "http_status",
                None,
            )

            failure_kind = classify_cache_failure(
                exception=exc,
                payload=payload,
                message=str(
                    exc
                ),
                http_status=http_status,
            )

            diagnostic = _diagnostic(
                candidate,
                payload,
                str(
                    exc
                ),
            )

            record_result(
                CREATE_BM_OPERATION,
                candidate,
                success=False,
                reason=diagnostic,
                profile_id=(
                    clean_profile_id
                ),
                failure_kind=(
                    failure_kind
                ),
            )

            diagnostics.append(
                diagnostic
            )

            if (
                failure_kind
                == "stale_schema"
            ):
                continue

            raise

        business_id, response_path = (
            _extract_create_business_id(
                response
            )
        )

        if business_id:
            persisted_candidate = (
                candidate
            )

            if (
                candidate.source
                == "job_manual"
                and response_path
                == "data.bizkit_create_business.id"
            ):
                persisted_candidate = upsert_candidate(
                    CREATE_BM_OPERATION,
                    doc_id=(
                        candidate.doc_id
                    ),
                    friendly_name=(
                        candidate.friendly_name
                    ),
                    endpoint_url=(
                        candidate.endpoint_url
                    ),
                    variables_mode=(
                        candidate.variables_mode
                    ),
                    source=(
                        "manual_success"
                    ),
                    priority=9_500,
                    observed_at=str(
                        int(
                            time.time()
                        )
                    ),
                )

            elif candidate.source.startswith(
                "dynamic_"
            ):
                persisted_candidate = upsert_candidate(
                    CREATE_BM_OPERATION,
                    doc_id=(
                        candidate.doc_id
                    ),
                    friendly_name=(
                        candidate.friendly_name
                    ),
                    endpoint_url=(
                        candidate.endpoint_url
                    ),
                    variables_mode=(
                        candidate.variables_mode
                    ),
                    source=(
                        "dynamic_success"
                    ),
                    priority=9_700,
                    observed_at=str(
                        int(
                            time.time()
                        )
                    ),
                )

            record_result(
                CREATE_BM_OPERATION,
                persisted_candidate,
                success=True,
                response_path=(
                    response_path
                ),
                profile_id=(
                    clean_profile_id
                ),
            )

            return CreateBusinessResult(
                business_id=(
                    business_id
                ),
                candidate=(
                    persisted_candidate
                ),
                response=response,
                response_path=(
                    response_path
                ),
            )

        errors = _graphql_errors(
            response
        )

        failure_kind = (
            classify_cache_failure(
                payload=response,
                message=(
                    "CREATE_BM returned GraphQL errors"
                ),
            )
            if errors
            else "other"
        )

        diagnostic = _diagnostic(
            candidate,
            response,
            (
                "CREATE_BM returned no "
                "data.bizkit_create_business.id"
            ),
        )

        record_result(
            CREATE_BM_OPERATION,
            candidate,
            success=False,
            reason=diagnostic,
            profile_id=(
                clean_profile_id
            ),
            failure_kind=(
                failure_kind
            ),
        )

        diagnostics.append(
            diagnostic
        )

        if (
            failure_kind
            == "stale_schema"
        ):
            continue

        data = response.get(
            "data"
        )

        if (
            isinstance(
                data,
                dict,
            )
            and data
        ):
            raise BusinessMutationError(
                "CREATE_RESULT_UNKNOWN",
                (
                    "CREATE_BM returned data but ReMask could not prove "
                    "data.bizkit_create_business.id. CREATE will not be retried."
                ),
                retryable=False,
                payload=response,
                candidate=candidate,
            )

        raise BusinessMutationError(
            "CREATE_BM_META_ERROR",
            diagnostic,
            retryable=False,
            payload=response,
            candidate=candidate,
        )

    raise BusinessMutationError(
        "CREATE_BM_MUTATION_NOT_DISCOVERED",
        (
            "All available CREATE_BM candidates were rejected as stale/schema "
            "mismatches. No additional CREATE request is available. "
            + " || ".join(
                diagnostics[-4:]
            )
        ),
        retryable=False,
    )


async def create_business_manager_v2(
    session: Any,
    *,
    params: dict[str, Any],
    profile_id: str = "",
) -> CreateBusinessResult:
    clean_params = (
        params
        if isinstance(
            params,
            dict,
        )
        else {}
    )

    manual_doc_id = _clean(
        clean_params.get(
            "manual_doc_id"
        )
        or clean_params.get(
            "doc_id"
        )
    )

    return await create_business_with_docids(
        session,
        business_name=_clean(
            clean_params.get(
                "name"
            )
            or clean_params.get(
                "bm_name"
            )
        ),
        user_email=_clean(
            clean_params.get(
                "user_email"
            )
            or clean_params.get(
                "email"
            )
        ),
        user_first_name=_clean(
            clean_params.get(
                "user_first_name"
            )
            or clean_params.get(
                "first_name"
            )
        ),
        user_last_name=_clean(
            clean_params.get(
                "user_last_name"
            )
            or clean_params.get(
                "last_name"
            )
        ),
        profile_display_name=_clean(
            clean_params.get(
                "profile_display_name"
            )
        ),
        manual_doc_id=(
            manual_doc_id
        ),
        profile_id=(
            profile_id
        ),
    )


async def attach_page_to_business(
    session: Any,
    *,
    business_id: str,
    business_name: str,
    page_id: str,
    profile_id: str = "",
) -> AttachPageResult:
    clean_profile_id = _profile_id(
        session,
        profile_id,
    )

    bootstrap = await session.bootstrap()

    actor_id = _clean(
        getattr(
            bootstrap,
            "actor_id",
            "",
        )
    )

    variables = _build_attach_variables(
        actor_id=actor_id,
        business_id=business_id,
        business_name=business_name,
        page_id=page_id,
    )

    dynamic_candidate: (
        DocIdCandidate
        | None
    )

    try:
        dynamic_candidate = (
            await asyncio.wait_for(
                discover_current_set_primary_page_candidate(
                    session,
                    business_id=(
                        business_id
                    ),
                ),
                timeout=20.0,
            )
        )
    except Exception:
        dynamic_candidate = None

    ordered: list[
        DocIdCandidate
    ] = []

    if dynamic_candidate is not None:
        ordered.append(
            dynamic_candidate
        )

    ordered.extend(
        list_candidates(
            SET_PRIMARY_PAGE_OPERATION,
            confirmed_only=True,
        )
    )

    candidates = _unique_candidates(
        ordered
    )

    if not candidates:
        raise BusinessMutationError(
            "SET_PRIMARY_PAGE_MUTATION_NOT_DISCOVERED",
            (
                "No current SET_PRIMARY_PAGE mutation was discovered and "
                "no previously confirmed cache candidate exists."
            ),
            retryable=True,
        )

    diagnostics: list[str] = []

    for candidate in candidates:
        try:
            response = await session.graphql(
                candidate.doc_id,
                variables,
                friendly_name=(
                    candidate.friendly_name
                ),
                endpoint_url=(
                    candidate.endpoint_url
                ),
            )

        except Exception as exc:
            payload = getattr(
                exc,
                "meta_payload",
                None,
            )

            if not isinstance(
                payload,
                dict,
            ):
                payload = {}

            failure_kind = classify_cache_failure(
                exception=exc,
                payload=payload,
                message=str(
                    exc
                ),
                http_status=getattr(
                    exc,
                    "http_status",
                    None,
                ),
            )

            diagnostic = _diagnostic(
                candidate,
                payload,
                str(
                    exc
                ),
            )

            record_result(
                SET_PRIMARY_PAGE_OPERATION,
                candidate,
                success=False,
                reason=diagnostic,
                profile_id=(
                    clean_profile_id
                ),
                failure_kind=(
                    failure_kind
                ),
            )

            diagnostics.append(
                diagnostic
            )

            if (
                failure_kind
                == "stale_schema"
            ):
                continue

            raise

        errors = _graphql_errors(
            response
        )

        if errors:
            failure_kind = classify_cache_failure(
                payload=response,
                message=(
                    "SET_PRIMARY_PAGE returned GraphQL errors"
                ),
            )

            diagnostic = _diagnostic(
                candidate,
                response,
                (
                    "SET_PRIMARY_PAGE returned GraphQL errors"
                ),
            )

            record_result(
                SET_PRIMARY_PAGE_OPERATION,
                candidate,
                success=False,
                reason=diagnostic,
                profile_id=(
                    clean_profile_id
                ),
                failure_kind=(
                    failure_kind
                ),
            )

            diagnostics.append(
                diagnostic
            )

            if (
                failure_kind
                == "stale_schema"
            ):
                continue

            raise BusinessMutationError(
                "SET_PRIMARY_PAGE_META_ERROR",
                diagnostic,
                retryable=True,
                payload=response,
                candidate=candidate,
            )

        persisted_candidate = (
            candidate
        )

        if candidate.source.startswith(
            "dynamic_"
        ):
            persisted_candidate = upsert_candidate(
                SET_PRIMARY_PAGE_OPERATION,
                doc_id=(
                    candidate.doc_id
                ),
                friendly_name=(
                    candidate.friendly_name
                ),
                endpoint_url=(
                    candidate.endpoint_url
                ),
                variables_mode=(
                    candidate.variables_mode
                ),
                source=(
                    "dynamic_success"
                ),
                priority=9_700,
                observed_at=str(
                    int(
                        time.time()
                    )
                ),
            )

        record_result(
            SET_PRIMARY_PAGE_OPERATION,
            persisted_candidate,
            success=True,
            response_path="data",
            profile_id=(
                clean_profile_id
            ),
        )

        return AttachPageResult(
            business_id=_clean(
                business_id
            ),
            page_id=_clean(
                page_id
            ),
            candidate=(
                persisted_candidate
            ),
            response=response,
        )

    raise BusinessMutationError(
        "SET_PRIMARY_PAGE_MUTATION_NOT_DISCOVERED",
        (
            "All SET_PRIMARY_PAGE candidates were rejected as stale/schema "
            "mismatches. "
            + " || ".join(
                diagnostics[-4:]
            )
        ),
        retryable=True,
    )


async def set_business_primary_page(
    session: Any,
    *,
    business_id: str,
    business_name: str,
    page_id: str,
) -> DocIdCandidate:
    result = await attach_page_to_business(
        session,
        business_id=business_id,
        business_name=business_name,
        page_id=page_id,
    )

    return result.candidate
