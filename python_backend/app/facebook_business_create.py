# python_backend/app/facebook_business_create.py

from __future__ import annotations

import asyncio
import html
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

# Captured from a live Meta Business creation request on 2026-09-24.
# This is only used when runtime discovery/cache/manual override produced no
# candidate at all; stale-schema handling will reject it instead of guessing.
CAPTURED_CREATE_BM_DOC_ID = "28057338880523368"

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
    def __init__(
        self,
        message: str,
        *,
        code: str = "DOC_ID_MUTATION_ERROR",
        retryable: bool = False,
        payload: dict[str, Any] | None = None,
        candidate: DocIdCandidate | None = None,
        stale_candidate: bool = False,
    ) -> None:
        super().__init__(
            code,
            message,
            retryable=retryable,
            payload=payload,
            candidate=candidate,
        )
        self.stale_candidate = bool(stale_candidate)


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
    if candidate.variables_mode in {
        "scope_selector_business_creation_v1",
        "scope_selector_footer_v2",
        "scope_selector_footer_v4",
        "scope_selector_footer_v6_browser_native",
        "scope_selector_footer_v6_browser_native",
    }:
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


def _candidate_is_stale_or_schema_mismatch(
    payload: dict[str, Any] | None,
    message: str = "",
) -> bool:
    body = payload if isinstance(payload, dict) else {}

    def walk(value: Any) -> list[str]:
        output: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {
                    "message",
                    "description",
                    "summary",
                    "errorsummary",
                    "errordescription",
                    "error_user_msg",
                    "error_user_title",
                    "type",
                }:
                    text = str(child or "").strip()
                    if text:
                        output.append(text)
                output.extend(walk(child))
        elif isinstance(value, list):
            for child in value:
                output.extend(walk(child))
        return output

    raw_errors = body.get("errors")
    if isinstance(raw_errors, list):
        for error in raw_errors:
            if not isinstance(error, dict):
                continue
            try:
                code = int(error.get("code"))
            except (TypeError, ValueError):
                code = None
            if code == 1357054 and bool(error.get("isNotCritical")):
                return True

    text = " ".join([*walk(body), str(message or "")]).lower()
    markers = (
        "persistedquerynotfound",
        "persisted query not found",
        "persisted query",
        "query not found",
        "unknown query",
        "unknown document",
        "unknown field",
        "unknown argument",
        "document id",
        "invalid document",
        "cannot query field",
        "expected type",
        "was not provided",
        "operation not found",
    )
    return any(marker in text for marker in markers)




def _page_backed_source_variants(source: str) -> list[str]:
    raw = str(source or "")
    variants = [raw]

    entity_decoded = html.unescape(raw)
    if entity_decoded not in variants:
        variants.append(entity_decoded)

    def decode_ascii_unicode(match: re.Match[str]) -> str:
        value = int(match.group(1), 16)
        return chr(value) if value <= 0x7F else match.group(0)

    decoded = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        decode_ascii_unicode,
        entity_decoded,
    )
    decoded = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: chr(int(match.group(1), 16)),
        decoded,
    )
    decoded = (
        decoded
        .replace(r"\/", "/")
        .replace(r'\"', '"')
        .replace(r"\'", "'")
    )
    if decoded not in variants:
        variants.append(decoded)

    return variants


def _extract_page_backed_create_docid(
    source: str,
) -> tuple[str, str]:
    best: tuple[int, str, str] | None = None

    create_markers = (
        "businessmanagercreatemutation",
        "bizkit_create_business",
        "business_manager_create",
        "create_business",
        "businesscreation",
        "business_creation",
        "business creation",
    )
    update_markers = (
        "bizkitsettingsupdatebusinessbasicinfomutation",
        "updatebusiness",
        "update_business",
        "businessbasicinfo",
    )

    for text in _page_backed_source_variants(source):
        if "primary_page_id" not in text:
            continue

        for marker in re.finditer(
            "primary_page_id",
            text,
            flags=re.IGNORECASE,
        ):
            left = max(0, marker.start() - 18000)
            right = min(len(text), marker.end() + 18000)
            window = text[left:right]
            lower_window = window.lower()

            if "business" not in lower_window:
                continue
            if not any(value in lower_window for value in create_markers):
                continue
            if (
                any(value in lower_window for value in update_markers)
                and "business_id" in lower_window
            ):
                continue

            doc_matches: list[re.Match[str]] = []
            doc_patterns = (
                r'(?:"|\')?(?:doc_id|docID)(?:"|\')?\s*[:=]\s*'
                r'(?:"|\')([0-9]{5,40})(?:"|\')',
                r'params\s*:\s*\{.{0,3000}?id\s*:\s*["\']([0-9]{5,40})["\']',
                r'["\']id["\']\s*:\s*["\']([0-9]{5,40})["\']',
            )
            for pattern in doc_patterns:
                doc_matches.extend(
                    re.finditer(
                        pattern,
                        window,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                )

            if not doc_matches:
                continue

            friendly = ""
            friendly_patterns = (
                r'fb_api_req_friendly_name(?:"|\')?\s*[:=]\s*'
                r'["\']([^"\']*Business[^"\']*(?:Create|Creation)[^"\']*)["\']',
                r'["\']name["\']\s*:\s*'
                r'["\']([^"\']*Business[^"\']*(?:Create|Creation)[^"\']*)["\']',
                r'["\']([^"\']*Business[^"\']*(?:Create|Creation)[^"\']*Mutation)["\']',
            )
            for pattern in friendly_patterns:
                match = re.search(
                    pattern,
                    window,
                    flags=re.IGNORECASE,
                )
                if match:
                    friendly = _clean(match.group(1))
                    break

            for doc_match in doc_matches:
                value = _clean(doc_match.group(1))
                absolute = left + doc_match.start(1)
                distance = abs(absolute - marker.start())
                score = distance

                if friendly:
                    score = max(0, score - 4000)

                candidate = (score, value, friendly)
                if best is None or candidate[0] < best[0]:
                    best = candidate

    if best is None:
        return "", ""

    return best[1], best[2]

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

    # Meta currently returns Business creation success through more than one
    # Relay response shape. The browser observer already supports these exact
    # shapes; the private GraphQL path must parse the same successful payloads.
    known_nodes = (
        "bizkit_create_business",
        "business_create",
        "business_manager_create",
    )

    matches: list[tuple[str, str]] = []

    for node_name in known_nodes:
        node = data.get(node_name)

        if not isinstance(node, dict):
            continue

        direct = _clean(node.get("id"))
        if direct.isdigit():
            matches.append(
                (
                    direct,
                    f"data.{node_name}.id",
                )
            )

        business = node.get("business")
        if isinstance(business, dict):
            nested = _clean(business.get("id"))
            if nested.isdigit():
                matches.append(
                    (
                        nested,
                        f"data.{node_name}.business.id",
                    )
                )

    unique_ids = {
        business_id
        for business_id, _ in matches
    }

    if len(unique_ids) != 1:
        return "", ""

    business_id = next(iter(unique_ids))
    for value, path in matches:
        if value == business_id:
            return business_id, path

    return "", ""


def _build_create_variables(
    *,
    actor_id: str,
    business_name: str,
    user_email: str,
    user_first_name: str,
    user_last_name: str,
    profile_display_name: str,
    qpl_join_id: str = "",
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

    payload = {
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
                "MBS_BUSINESS_CREATION_IN_SCOPE_SELECTOR_FOOTER"
            ),
            "entry_point": (
                "BIZWEB_SCOPE_SELECTOR_FOOTER_CREATION_BUTTON"
            ),
        }
    }

    clean_qpl_join_id = _clean(qpl_join_id)
    payload["input"]["qpl_join_id"] = clean_qpl_join_id or str(uuid.uuid4())

    return payload


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
        max_scripts_per_entry=32,
        script_max_bytes=3_000_000,
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
            "scope_selector_footer_v6_browser_native"
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
            "https://www.facebook.com/",
            "https://business.facebook.com/latest/home",
        ],
        max_scripts_per_entry=24,
        script_max_bytes=2_000_000,
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
    qpl_join_id: str = "",
    request_envelope: dict[str, Any] | None = None,
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
        qpl_join_id=qpl_join_id,
    )

    captured_envelope = (
        request_envelope
        if isinstance(request_envelope, dict)
        else {}
    )
    bootstrap_envelope = (
        getattr(
            bootstrap,
            "request_context",
            {},
        )
        or {}
    )
    envelope_keys = sorted(
        {
            *[
                str(key)
                for key in bootstrap_envelope
                if str(key).strip()
            ],
            *[
                str(key)
                for key in captured_envelope
                if str(key).strip()
            ],
        }
    )
    create_payload_meta = (
        "payload_version=scope_selector_footer_v6_browser_native "
        f"transport=browser_native "
        f"qpl={'captured' if _clean(qpl_join_id) else 'generated_uuid4'} "
        f"envelope_source={'captured' if captured_envelope else 'browser_or_bootstrap'} "
        f"envelope={','.join(envelope_keys) if envelope_keys else '-'}"
    )

    confirmed_cache = list_candidates(
        CREATE_BM_OPERATION,
        confirmed_only=True,
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
                timeout=(4.0 if confirmed_cache else 20.0),
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
                    "scope_selector_footer_v6_browser_native"
                ),
                source="job_manual",
                priority=19_000,
                observed_at="runtime",
                enabled=True,
            )
        )

    ordered.extend(
        confirmed_cache
    )

    candidates = _unique_candidates(
        ordered
    )

    if not candidates:
        # We already have an exact live capture of Meta's current Business
        # creation mutation in the repository test fixture. Runtime HTML/JS
        # discovery is not guaranteed to expose Relay metadata for every
        # account/A-B shell, so use the same captured request doc_id as a
        # single bounded fallback instead of failing before any CREATE reaches
        # Meta.
        candidates = [
            DocIdCandidate(
                operation=CREATE_BM_OPERATION,
                doc_id=CAPTURED_CREATE_BM_DOC_ID,
                friendly_name=CREATE_BM_FRIENDLY_NAME,
                endpoint_url=BUSINESS_GRAPHQL_URL,
                variables_mode="scope_selector_footer_v6_browser_native",
                source="live_capture_2026_09_24",
                priority=8_000,
                observed_at="2026-09-24",
                enabled=True,
            )
        ]

    diagnostics: list[str] = []

    for candidate in candidates:
        try:
            browser_graphql = getattr(
                session,
                "graphql_browser_native",
                None,
            )
            if not callable(browser_graphql):
                raise BusinessMutationError(
                    "CREATE_BM_BROWSER_TRANSPORT_UNAVAILABLE",
                    (
                        "CREATE_BM requires browser-native transport, "
                        "but the current worker does not provide it."
                    ),
                    retryable=False,
                )

            response = await browser_graphql(
                candidate.doc_id,
                variables,
                friendly_name=(
                    candidate.friendly_name
                ),
                endpoint_url=(
                    candidate.endpoint_url
                ),
                request_envelope=(
                    captured_envelope
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
                (
                    f"{exc} {create_payload_meta}"
                ),
            )

            if candidate.source != "job_manual":
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
                stored_candidate = upsert_candidate(
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
                if isinstance(stored_candidate, DocIdCandidate):
                    persisted_candidate = stored_candidate

            elif (
                candidate.source.startswith("dynamic_")
                or candidate.source.startswith("live_capture_")
            ):
                stored_candidate = upsert_candidate(
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
                        "live_capture_success"
                        if candidate.source.startswith("live_capture_")
                        else "dynamic_success"
                    ),
                    priority=9_700,
                    observed_at=str(
                        int(
                            time.time()
                        )
                    ),
                )
                if isinstance(stored_candidate, DocIdCandidate):
                    persisted_candidate = stored_candidate

            if (
                candidate.source != "job_manual"
                or response_path == "data.bizkit_create_business.id"
            ):
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
                "data.bizkit_create_business.id "
                + create_payload_meta
            ),
        )

        if candidate.source != "job_manual":
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
                    "CREATE_BM returned data but ReMask could not prove a Business ID "
                    "from known create response paths. CREATE will not be retried."
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
        qpl_join_id=_clean(
            clean_params.get(
                "qpl_join_id"
            )
        ),
        request_envelope=(
            clean_params.get("request_envelope")
            if isinstance(
                clean_params.get("request_envelope"),
                dict,
            )
            else {}
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
                retryable=(failure_kind == "network"),
                payload=response,
                candidate=candidate,
            )

        data = response.get("data")
        if not isinstance(data, dict) or not data:
            diagnostic = _diagnostic(
                candidate,
                response,
                "SET_PRIMARY_PAGE returned no confirmable data",
            )
            record_result(
                SET_PRIMARY_PAGE_OPERATION,
                candidate,
                success=False,
                reason=diagnostic,
                profile_id=clean_profile_id,
                failure_kind="other",
            )
            raise BusinessMutationError(
                "SET_PRIMARY_PAGE_RESULT_UNKNOWN",
                (
                    "SET_PRIMARY_PAGE returned no GraphQL errors but also no "
                    "confirmable data. Business creation must not be repeated; "
                    "Page attachment should be verified through Business Settings."
                ),
                retryable=False,
                payload=response,
                candidate=candidate,
            )

        persisted_candidate = (
            candidate
        )

        if candidate.source.startswith(
            "dynamic_"
        ):
            stored_candidate = upsert_candidate(
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
            if isinstance(stored_candidate, DocIdCandidate):
                persisted_candidate = stored_candidate

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
