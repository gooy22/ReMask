from __future__ import annotations

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
    text = _error_text(payload or {}, message).lower()

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

    candidates = _prefer_page_backed_candidates(
        list_candidates("CREATE_BM"),
        page_id=page_id,
    )

    if not allow_scope_selector_fallback:
        candidates = [
            candidate
            for candidate in candidates
            if candidate_requirements(candidate).get("page_id") is True
        ]

    if explicit_doc_id:
        explicit = _clean(explicit_doc_id)
        candidates = [
            candidate
            for candidate in candidates
            if candidate.doc_id == explicit
        ]

        if not candidates:
            if not allow_scope_selector_fallback:
                raise DocIdMutationError(
                    "Explicit CREATE_BM doc_id is not registered as a Page-backed "
                    "mutation; refusing non-Page fallback"
                )

            # An explicit caller-provided doc_id is allowed even when it is not
            # yet in the registry. Use the current scope-selector contract.
            from .facebook_docids import DocIdCandidate

            candidates = [
                DocIdCandidate(
                    operation="CREATE_BM",
                    doc_id=explicit,
                    friendly_name="useBusinessCreationMutationMutation",
                    endpoint_url="https://business.facebook.com/api/graphql/",
                    variables_mode="scope_selector_business_creation_v1",
                    source="explicit_call",
                    priority=20_000,
                    observed_at="runtime",
                )
            ]

    if not candidates:
        raise DocIdMutationError(
            "No CREATE_BM doc_id candidates are configured"
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

            if _candidate_is_stale_or_schema_mismatch(
                payload,
                str(exc),
            ):
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
                )
                stale_failures.append(reason)
                continue

            raise

        business_id, response_path = extract_business_id(response)
        response_errors = _errors(response)

        if business_id:
            record_result(
                "CREATE_BM",
                candidate,
                success=True,
                response_path=response_path,
            )
            return CreateBusinessResult(
                business_id=business_id,
                candidate=candidate,
                response=response,
                response_path=response_path,
            )

        # A response with data may mean the mutation executed but our parser no
        # longer recognizes the shape. Never auto-retry another mutation in that
        # situation because that could create a duplicate Business.
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
            )
            raise DocIdMutationError(
                reason,
                payload=response,
                candidate=candidate,
                stale_candidate=False,
            )

        if response_errors and _candidate_is_stale_or_schema_mismatch(
            response,
            "",
        ):
            reason = _diagnostic(
                candidate,
                response,
                "stale doc_id or variables schema",
            )
            record_result(
                "CREATE_BM",
                candidate,
                success=False,
                reason=reason,
            )
            stale_failures.append(reason)
            continue

        reason = _diagnostic(
            candidate,
            response,
            "CREATE_BM returned no Business ID",
        )
        record_result(
            "CREATE_BM",
            candidate,
            success=False,
            reason=reason,
        )
        raise DocIdMutationError(
            reason,
            payload=response,
            candidate=candidate,
            stale_candidate=False,
        )

    if stale_failures and not explicit_doc_id:
        discovery_specs: list[tuple[str, str]] = []
        if _clean(page_id):
            discovery_specs.append(
                ("BusinessManagerCreateMutation", "legacy_primary_page_v1")
            )
        if allow_scope_selector_fallback:
            discovery_specs.append(
                (
                    "useBusinessCreationMutationMutation",
                    "scope_selector_business_creation_v1",
                )
            )

        for friendly_name, variables_mode in discovery_specs:
            discovered = await discover_persisted_query(
                session,
                friendly_name=friendly_name,
                entry_urls=[
                    "https://business.facebook.com/latest/home",
                    "https://business.facebook.com/latest/settings",
                    "https://business.facebook.com/latest/overview",
                ],
                max_scripts_per_entry=28,
            )

            if discovered is None:
                continue

            refreshed = upsert_candidate(
                "CREATE_BM",
                doc_id=discovered.doc_id,
                friendly_name=friendly_name,
                endpoint_url="https://business.facebook.com/api/graphql/",
                variables_mode=variables_mode,
                source=f"runtime_{discovered.source_kind}",
                priority=8_500,
                observed_at=str(int(time.time())),
            )

            if all(
                candidate.doc_id != refreshed.doc_id
                or candidate.variables_mode != refreshed.variables_mode
                for candidate in candidates
            ):
                return await create_business_with_docids(
                    session,
                    business_name=business_name,
                    page_id=page_id,
                    user_email=user_email,
                    user_first_name=user_first_name,
                    user_last_name=user_last_name,
                    profile_display_name=profile_display_name,
                    vertical=vertical,
                    explicit_doc_id=refreshed.doc_id,
                    allow_scope_selector_fallback=allow_scope_selector_fallback,
                )

    details = []
    if skipped:
        details.append("skipped=" + " || ".join(skipped))
    if stale_failures:
        details.append(
            "stale_candidates=" + " || ".join(stale_failures)
        )

    if not allow_scope_selector_fallback:
        raise DocIdMutationError(
            "No usable Page-backed CREATE_BM mutation is available. "
            + " ".join(details)
        )

    raise DocIdMutationError(
        "No usable CREATE_BM doc_id candidate. " + " ".join(details)
    )
