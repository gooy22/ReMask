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
    list_candidates,
    record_result,
    upsert_candidate,
)
from .facebook_query_discovery import discover_persisted_query


class DocIdMutationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        candidate: DocIdCandidate | None = None,
        stale_candidate: bool = False,
    ) -> None:
        super().__init__(message)
        self.payload = payload or {}
        self.candidate = candidate
        self.stale_candidate = stale_candidate


@dataclass(slots=True)
class CreateBusinessResult:
    business_id: str
    candidate: DocIdCandidate
    response: dict[str, Any]
    response_path: str


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _get_path(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


CREATE_BM_ID_PATHS: tuple[tuple[str, ...], ...] = (
    ("data", "business_create", "business", "id"),
    ("data", "business_create", "id"),
    ("data", "bizkit_create_business", "business", "id"),
    ("data", "bizkit_create_business", "id"),
    ("data", "business_manager_create", "business", "id"),
    ("data", "business_manager_create", "id"),
    ("data", "create_business", "business", "id"),
    ("data", "create_business", "id"),
)


def extract_business_id(
    payload: dict[str, Any],
) -> tuple[str, str]:
    for path in CREATE_BM_ID_PATHS:
        value = _clean(_get_path(payload, path))
        if value and value.isdigit():
            return value, ".".join(path)

    # Conservative fallback: only accept an object explicitly typed/named as a
    # Business. Never take the first arbitrary "id" from a mutation response.
    stack: list[tuple[str, Any]] = [("data", payload.get("data"))]
    while stack:
        path, node = stack.pop()

        if isinstance(node, dict):
            typename = _clean(node.get("__typename")).lower()
            business_name = _clean(node.get("name"))
            business_id = _clean(node.get("id"))

            if (
                business_id.isdigit()
                and (
                    typename in {"business", "businessmanager", "business_manager"}
                    or (
                        "business" in path.lower()
                        and business_name
                    )
                )
            ):
                return business_id, f"{path}.id"

            for key, child in node.items():
                if isinstance(child, (dict, list)):
                    stack.append((f"{path}.{key}", child))

        elif isinstance(node, list):
            for index, child in enumerate(node):
                if isinstance(child, (dict, list)):
                    stack.append((f"{path}[{index}]", child))

    return "", ""


def _errors(payload: dict[str, Any]) -> list[Any]:
    raw = payload.get("errors")
    if isinstance(raw, list):
        return raw
    if raw:
        return [raw]
    if payload.get("error"):
        return [payload.get("error")]
    return []


def _error_text(payload: dict[str, Any], fallback: str = "") -> str:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {
                    "message",
                    "description",
                    "summary",
                    "error_user_msg",
                    "error_user_title",
                    "type",
                }:
                    text = _clean(child)
                    if text:
                        values.append(text)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    if fallback:
        values.append(fallback)
    return " | ".join(values)


def _candidate_is_stale_or_schema_mismatch(
    payload: dict[str, Any] | None,
    message: str,
) -> bool:
    body = payload or {}
    text = _error_text(body, message).lower()

    # Facebook generic "request could not be processed" on an old persisted
    # mutation commonly arrives as 1357054 + isNotCritical=1 with no data.
    # That is safe to treat as a stale candidate signal for discovery/retry.
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

    markers = (
        "persistedquerynotfound",
        "persisted query",
        "query not found",
        "unknown query",
        "unknown document",
        "document id",
        "document_id",
        "doc_id",
        "invalid doc",
        "invalid document",
        "unknown argument",
        "unknown field",
        "variable ",
        "variable $",
        "was not provided",
        "expected type",
        "required type",
        "got invalid value",
        "cannot query field",
        "does not exist on type",
        "operation not found",
    )
    return any(marker in text for marker in markers)


def _derive_name_parts(
    display_name: str,
    first_name: str,
    last_name: str,
) -> tuple[str, str]:
    first = _clean(first_name)
    last = _clean(last_name)

    if first and last:
        return first, last

    parts = [part for part in re.split(r"\s+", _clean(display_name)) if part]
    if not first and parts:
        first = parts[0]
    if not last:
        if len(parts) >= 2:
            last = parts[-1]
        else:
            last = "Business"

    return first or "Business", last or "Owner"


def candidate_requirements(
    candidate: DocIdCandidate,
) -> dict[str, bool]:
    mode = candidate.variables_mode

    if mode == "scope_selector_business_creation_v1":
        return {
            "email": True,
            "page_id": False,
        }

    if mode == "legacy_primary_page_v1":
        return {
            "email": False,
            "page_id": True,
        }

    return {
        "email": False,
        "page_id": False,
    }


def _prefer_page_backed_candidates(
    candidates: list[DocIdCandidate],
    *,
    page_id: str,
) -> list[DocIdCandidate]:
    if not _clean(page_id):
        return candidates

    # The user explicitly selected a Fan Page. Prefer a mutation contract that
    # actually carries primary_page_id. Keep the original registry ordering
    # within each group, so previous-success/priority scoring still matters.
    return sorted(
        candidates,
        key=lambda candidate: (
            0 if candidate_requirements(candidate).get("page_id") else 1
        ),
    )


def build_create_business_variables(
    candidate: DocIdCandidate,
    *,
    actor_id: str,
    business_name: str,
    page_id: str = "",
    user_email: str = "",
    user_first_name: str = "",
    user_last_name: str = "",
    profile_display_name: str = "",
    vertical: str = "ADVERTISING",
) -> dict[str, Any]:
    actor = _clean(actor_id)
    name = _clean(business_name)
    page = _clean(page_id)
    email = _clean(user_email)
    vertical_value = _clean(vertical).upper() or "ADVERTISING"

    if not actor:
        raise ValueError("Facebook actor_id is required")
    if not name:
        raise ValueError("Business name is required")

    if candidate.variables_mode == "scope_selector_business_creation_v1":
        if not email:
            raise ValueError(
                "user_email is required by scope-selector Business creation mutation"
            )

        first, last = _derive_name_parts(
            profile_display_name,
            user_first_name,
            user_last_name,
        )

        return {
            "input": {
                "client_mutation_id": uuid.uuid4().hex[:16],
                "actor_id": actor,
                "business_name": name,
                "user_first_name": first,
                "user_last_name": last,
                "user_email": email,
                "creation_source": (
                    "MBS_BUSINESS_CREATION_IN_SCOPE_SELECTOR_FOOTER"
                ),
                "entry_point": (
                    "BIZWEB_SCOPE_SELECTOR_FOOTER_CREATION_BUTTON"
                ),
            }
        }

    if candidate.variables_mode == "legacy_primary_page_v1":
        if not page:
            raise ValueError(
                "Primary Page ID is required by legacy Business mutation"
            )

        return {
            "input": {
                "name": name,
                "vertical": vertical_value,
                "primary_page_id": page,
                "client_mutation_id": "1",
            }
        }

    raise ValueError(
        f"Unsupported CREATE_BM variables_mode: {candidate.variables_mode}"
    )


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
            body = repr(payload)

    return (
        f"doc_id={candidate.doc_id} "
        f"friendly_name={candidate.friendly_name or '-'} "
        f"mode={candidate.variables_mode} "
        f"source={candidate.source} "
        f"endpoint={candidate.endpoint_url} "
        f"message={message or '-'} "
        f"payload={body[:5000]}"
    )


def _page_backed_source_variants(source: str) -> list[str]:
    raw = str(source or "")
    variants = [raw]

    entity_decoded = html.unescape(raw)
    if entity_decoded not in variants:
        variants.append(entity_decoded)

    def decode_ascii_unicode(match: re.Match[str]) -> str:
        value = int(match.group(1), 16)
        return chr(value) if value <= 0x7F else match.group(0)

    decoded = re.sub(r"\\u([0-9a-fA-F]{4})", decode_ascii_unicode, entity_decoded)
    decoded = re.sub(
        r"\\x([0-9a-fA-F]{2})",
        lambda match: chr(int(match.group(1), 16)),
        decoded,
    )
    decoded = (
        decoded
        .replace(r"\/", "/")
        .replace(r"\"", '"')
        .replace(r"\'", "'")
    )
    if decoded not in variants:
        variants.append(decoded)

    return variants


def _extract_page_backed_create_docid(
    source: str,
) -> tuple[str, str]:
    """
    Locate a current Business *creation* persisted query that carries
    primary_page_id. Avoid update/rename Business mutations which may also
    mention primary_page_id but operate on an existing business_id.
    """
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

        for marker in re.finditer("primary_page_id", text, flags=re.IGNORECASE):
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
                match = re.search(pattern, window, flags=re.IGNORECASE)
                if match:
                    friendly = _clean(match.group(1))
                    break

            for doc_match in doc_matches:
                value = _clean(doc_match.group(1))
                absolute = left + doc_match.start(1)
                distance = abs(absolute - marker.start())
                score = distance

                # Prefer IDs with a nearby creation operation name.
                if friendly:
                    score = max(0, score - 4000)

                candidate = (score, value, friendly)
                if best is None or candidate[0] < best[0]:
                    best = candidate

    if best is None:
        return "", ""
    return best[1], best[2]


async def discover_current_scope_selector_create_candidate(
    session: Any,
    *,
    max_scripts: int = 32,
) -> DocIdCandidate | None:
    discovered = await discover_persisted_query(
        session,
        friendly_name="useBusinessCreationMutationMutation",
        entry_urls=[
            "https://business.facebook.com/latest/home",
            "https://business.facebook.com/latest/settings",
            "https://business.facebook.com/latest/overview",
        ],
        max_scripts_per_entry=max_scripts,
    )
    if discovered is None:
        return None

    return upsert_candidate(
        "CREATE_BM",
        doc_id=discovered.doc_id,
        friendly_name="useBusinessCreationMutationMutation",
        endpoint_url="https://business.facebook.com/api/graphql/",
        variables_mode="scope_selector_business_creation_v1",
        source=f"runtime_{discovered.source_kind}",
        priority=9_700,
        observed_at=str(int(time.time())),
    )


async def discover_current_page_backed_create_candidate(
    session: Any,
    *,
    max_scripts: int = 0,
) -> DocIdCandidate | None:
    """
    v14 legacy discovery path.

    Only initial Facebook HTML and response headers are inspected. JavaScript
    bundles are never downloaded by the worker.
    """
    exact = await discover_persisted_query(
        session,
        friendly_name="BusinessManagerCreateMutation",
        entry_urls=[
            "https://www.facebook.com/",
            "https://business.facebook.com/latest/home",
        ],
        max_scripts_per_entry=0,
    )

    if exact is not None:
        return upsert_candidate(
            "CREATE_BM",
            doc_id=exact.doc_id,
            friendly_name="BusinessManagerCreateMutation",
            endpoint_url="https://business.facebook.com/api/graphql/",
            variables_mode="legacy_primary_page_v1",
            source=f"runtime_{exact.source_kind}",
            priority=9_500,
            observed_at=str(int(time.time())),
        )

    for entry_url in (
        "https://www.facebook.com/",
        "https://business.facebook.com/latest/home",
    ):
        try:
            if hasattr(session, "fetch_text_with_headers"):
                status, document, _, headers = await session.fetch_text_with_headers(
                    entry_url,
                    max_bytes=3_500_000,
                )
            else:
                status, document, _ = await session.fetch_text(
                    entry_url,
                    max_bytes=3_500_000,
                )
                headers = {}
        except Exception:
            continue

        if status >= 400:
            continue

        header_blob = "\n".join(
            f"{key}: {value}"
            for key, value in dict(headers or {}).items()
        )

        for source, source_kind in (
            (document, "html_marker"),
            (header_blob, "response_headers_marker"),
        ):
            doc_id, friendly = _extract_page_backed_create_docid(source)
            if not doc_id:
                continue

            return upsert_candidate(
                "CREATE_BM",
                doc_id=doc_id,
                friendly_name=friendly or "BusinessManagerCreateMutation",
                endpoint_url="https://business.facebook.com/api/graphql/",
                variables_mode="legacy_primary_page_v1",
                source=f"runtime_{source_kind}",
                priority=9_400,
                observed_at=str(int(time.time())),
            )

    return None


async def discover_current_set_primary_page_candidate(
    session: Any,
    *,
    business_id: str,
    max_scripts: int = 32,
) -> DocIdCandidate | None:
    clean_business_id = _clean(business_id)
    if not clean_business_id:
        return None

    discovered = await discover_persisted_query(
        session,
        friendly_name="BizKitSettingsUpdateBusinessBasicInfoMutation",
        entry_urls=[
            (
                "https://business.facebook.com/latest/settings/"
                f"business_info?business_id={clean_business_id}"
            ),
        ],
        max_scripts_per_entry=max_scripts,
    )
    if discovered is None:
        return None

    return upsert_candidate(
        "SET_PRIMARY_PAGE",
        doc_id=discovered.doc_id,
        friendly_name="BizKitSettingsUpdateBusinessBasicInfoMutation",
        endpoint_url="https://business.facebook.com/api/graphql/",
        variables_mode="bizkit_settings_update_business_basic_info_v1",
        source=f"runtime_{discovered.source_kind}",
        priority=9_700,
        observed_at=str(int(time.time())),
    )


async def set_business_primary_page(
    session: Any,
    *,
    business_id: str,
    business_name: str,
    page_id: str,
) -> DocIdCandidate:
    clean_business_id = _clean(business_id)
    clean_business_name = _clean(business_name)
    clean_page = _clean(page_id)

    if not clean_business_id:
        raise DocIdMutationError(
            "business_id is required for primary Page attachment"
        )
    if not clean_page:
        raise DocIdMutationError(
            "page_id is required for primary Page attachment"
        )

    bootstrap = await session.bootstrap()
    actor_id = _clean(getattr(bootstrap, "actor_id", ""))
    if not actor_id:
        raise DocIdMutationError(
            "Facebook actor_id is unavailable for primary Page attachment"
        )

    runtime_candidate: DocIdCandidate | None = None
    try:
        runtime_candidate = await asyncio.wait_for(
            discover_current_set_primary_page_candidate(
                session,
                business_id=clean_business_id,
            ),
            timeout=25.0,
        )
    except Exception:
        runtime_candidate = None

    candidates = list_candidates("SET_PRIMARY_PAGE")
    if runtime_candidate is not None:
        candidates = [
            runtime_candidate,
            *[
                candidate
                for candidate in candidates
                if (
                    candidate.doc_id != runtime_candidate.doc_id
                    or candidate.variables_mode
                    != runtime_candidate.variables_mode
                )
            ],
        ]

    if not candidates:
        raise DocIdMutationError(
            "No SET_PRIMARY_PAGE mutation candidate is available"
        )

    failures: list[str] = []
    for candidate in candidates:
        variables = {
            "input": {
                "client_mutation_id": uuid.uuid4().hex[:16],
                "actor_id": actor_id,
                "business_id": clean_business_id,
                "business_name": clean_business_name,
                "primary_page_id": clean_page,
                "entry_point": "BUSINESS_MANAGER_BUSINESS_INFO",
            }
        }

        try:
            response = await session.graphql(
                candidate.doc_id,
                variables,
                friendly_name=candidate.friendly_name,
                endpoint_url=candidate.endpoint_url,
            )
        except Exception as exc:
            payload = getattr(exc, "meta_payload", None)
            if not isinstance(payload, dict):
                payload = {}

            reason = _diagnostic(
                candidate,
                payload,
                str(exc),
            )
            record_result(
                "SET_PRIMARY_PAGE",
                candidate,
                success=False,
                reason=reason,
            )

            if _candidate_is_stale_or_schema_mismatch(
                payload,
                str(exc),
            ):
                failures.append(reason)
                continue

            raise DocIdMutationError(
                reason,
                payload=payload,
                candidate=candidate,
            ) from exc

        response_errors = _errors(response)
        if response_errors:
            reason = _diagnostic(
                candidate,
                response,
                "SET_PRIMARY_PAGE returned GraphQL errors",
            )
            record_result(
                "SET_PRIMARY_PAGE",
                candidate,
                success=False,
                reason=reason,
            )

            if _candidate_is_stale_or_schema_mismatch(
                response,
                "",
            ):
                failures.append(reason)
                continue

            raise DocIdMutationError(
                reason,
                payload=response,
                candidate=candidate,
            )

        record_result(
            "SET_PRIMARY_PAGE",
            candidate,
            success=True,
            response_path="data",
        )
        return candidate

    raise DocIdMutationError(
        "No usable SET_PRIMARY_PAGE mutation is available. "
        + " || ".join(failures[-4:])
    )


async def create_business_with_docids(
    session: Any,
    *,
    business_name: str,
    page_id: str = "",
    user_email: str = "",
    user_first_name: str = "",
    user_last_name: str = "",
    profile_display_name: str = "",
    vertical: str = "ADVERTISING",
    explicit_doc_id: str | None = None,
    allow_scope_selector_fallback: bool = True,
) -> CreateBusinessResult:
    bootstrap = await session.bootstrap()
    actor_id = _clean(getattr(bootstrap, "actor_id", ""))
    profile_id = _clean(
        getattr(getattr(session, "profile", None), "name", "")
    ) or "<unknown-profile>"
    has_page = bool(_clean(page_id))
    manual_doc_id = _clean(explicit_doc_id)

    runtime_candidate: DocIdCandidate | None = None
    if allow_scope_selector_fallback:
        try:
            runtime_candidate = await asyncio.wait_for(
                discover_current_scope_selector_create_candidate(session),
                timeout=20.0,
            )
        except Exception:
            runtime_candidate = None
    elif has_page:
        try:
            runtime_candidate = await asyncio.wait_for(
                discover_current_page_backed_create_candidate(session),
                timeout=20.0,
            )
        except Exception:
            runtime_candidate = None

    cached_candidates = list_candidates("CREATE_BM")

    if allow_scope_selector_fallback:
        cached_candidates = [
            candidate
            for candidate in cached_candidates
            if candidate.variables_mode
            == "scope_selector_business_creation_v1"
        ]
    elif has_page:
        cached_candidates = [
            candidate
            for candidate in cached_candidates
            if candidate_requirements(candidate).get("page_id") is True
        ]

    ordered: list[DocIdCandidate] = []

    # 1) Dynamic discovery from the initial Facebook HTML/headers.
    if runtime_candidate is not None:
        ordered.append(runtime_candidate)

    # 2) Manual Job override only when dynamic discovery returned nothing.
    if runtime_candidate is None and manual_doc_id:
        if not re.fullmatch(r"\d{5,40}", manual_doc_id):
            raise DocIdMutationError(
                "BUSINESS.manual_doc_id must contain 5-40 digits"
            )

        ordered.append(
            DocIdCandidate(
                operation="CREATE_BM",
                doc_id=manual_doc_id,
                friendly_name=(
                    "useBusinessCreationMutationMutation"
                    if allow_scope_selector_fallback
                    else "BusinessManagerCreateMutation"
                ),
                endpoint_url="https://business.facebook.com/api/graphql/",
                variables_mode=(
                    "scope_selector_business_creation_v1"
                    if allow_scope_selector_fallback
                    else "legacy_primary_page_v1"
                ),
                source="job_manual",
                priority=20_000,
                observed_at="runtime",
            )
        )

    # 3) Shared persisted/env cache is the final automatic source.
    ordered.extend(cached_candidates)

    candidates: list[DocIdCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in ordered:
        key = (
            candidate.doc_id,
            candidate.variables_mode,
            candidate.endpoint_url,
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    if not candidates:
        manual_hint = (
            " Provide parameters.BUSINESS.manual_doc_id to run a manual "
            "candidate."
            if not manual_doc_id
            else ""
        )
        raise DocIdMutationError(
            "No current CREATE_BM doc_id was found in Facebook initial HTML "
            "or response headers, and no usable cached candidate exists."
            + manual_hint
        )

    skipped: list[str] = []
    stale_failures: list[str] = []

    for candidate in candidates:
        requirements = candidate_requirements(candidate)

        if requirements["email"] and not _clean(user_email):
            skipped.append(
                f"{candidate.doc_id}: requires user_email "
                f"({candidate.variables_mode})"
            )
            continue

        if requirements["page_id"] and not _clean(page_id):
            skipped.append(
                f"{candidate.doc_id}: requires page_id "
                f"({candidate.variables_mode})"
            )
            continue

        try:
            variables = build_create_business_variables(
                candidate,
                actor_id=actor_id,
                business_name=business_name,
                page_id=page_id,
                user_email=user_email,
                user_first_name=user_first_name,
                user_last_name=user_last_name,
                profile_display_name=profile_display_name,
                vertical=vertical,
            )
        except ValueError as exc:
            skipped.append(f"{candidate.doc_id}: {exc}")
            continue

        try:
            response = await session.graphql(
                candidate.doc_id,
                variables,
                friendly_name=candidate.friendly_name,
                endpoint_url=candidate.endpoint_url,
            )
        except Exception as exc:
            payload = getattr(exc, "meta_payload", None)
            if not isinstance(payload, dict):
                payload = {}

            stale = _candidate_is_stale_or_schema_mismatch(
                payload,
                str(exc),
            )
            reason = _diagnostic(
                candidate,
                payload,
                str(exc),
            )
            record_result(
                "CREATE_BM",
                candidate,
                success=False,
                reason=reason,
                profile_id=profile_id,
                stale_failure=stale,
            )

            if stale:
                stale_failures.append(reason)
                continue
            raise

        business_id, response_path = extract_business_id(response)
        response_errors = _errors(response)

        if business_id:
            if candidate.source == "job_manual":
                upsert_candidate(
                    "CREATE_BM",
                    doc_id=candidate.doc_id,
                    friendly_name=candidate.friendly_name,
                    endpoint_url=candidate.endpoint_url,
                    variables_mode=candidate.variables_mode,
                    source="manual_success",
                    priority=8_800,
                    observed_at=str(int(time.time())),
                )

            record_result(
                "CREATE_BM",
                candidate,
                success=True,
                response_path=response_path,
                profile_id=profile_id,
            )
            return CreateBusinessResult(
                business_id=business_id,
                candidate=candidate,
                response=response,
                response_path=response_path,
            )

        data = response.get("data")
        if isinstance(data, dict) and data:
            reason = _diagnostic(
                candidate,
                response,
                "mutation returned data but no recognized Business ID",
            )
            record_result(
                "CREATE_BM",
                candidate,
                success=False,
                reason=reason,
                profile_id=profile_id,
            )
            raise DocIdMutationError(
                reason,
                payload=response,
                candidate=candidate,
                stale_candidate=False,
            )

        stale = bool(
            response_errors
            and _candidate_is_stale_or_schema_mismatch(
                response,
                "",
            )
        )
        reason = _diagnostic(
            candidate,
            response,
            (
                "stale doc_id or variables schema"
                if stale
                else "CREATE_BM returned no Business ID"
            ),
        )
        record_result(
            "CREATE_BM",
            candidate,
            success=False,
            reason=reason,
            profile_id=profile_id,
            stale_failure=stale,
        )

        if stale:
            stale_failures.append(reason)
            continue

        raise DocIdMutationError(
            reason,
            payload=response,
            candidate=candidate,
            stale_candidate=False,
        )

    details: list[str] = []
    if skipped:
        details.append("skipped=" + " || ".join(skipped))
    if stale_failures:
        details.append(
            "stale_candidates=" + " || ".join(stale_failures)
        )

    raise DocIdMutationError(
        "No usable CREATE_BM candidate completed successfully. "
        + " ".join(details)
    )

