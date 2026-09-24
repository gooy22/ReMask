# python_backend/app/facebook_ad_account_create.py

from __future__ import annotations

import asyncio
import json
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
from .facebook_query_discovery import discover_persisted_query


CREATE_AD_ACCOUNT_OPERATION = "CREATE_AD_ACCOUNT"
CREATE_AD_ACCOUNT_FRIENDLY_NAME = "AdAccountCreateMutation"
BUSINESS_GRAPHQL_URL = "https://business.facebook.com/api/graphql/"

# Legacy value that existed in ReMask before the live-discovery path.
# It is deliberately the last fallback and is never treated as confirmed
# until Meta returns a real Ad Account ID.
LEGACY_CREATE_AD_ACCOUNT_DOC_ID = "684920184730193"


class AdAccountMutationError(RuntimeError):
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
        self.code = str(code or "AD_ACCOUNT_MUTATION_FAILED")
        self.retryable = bool(retryable)
        self.payload = payload if isinstance(payload, dict) else {}
        self.candidate = candidate


@dataclass(slots=True)
class CreateAdAccountResult:
    ad_account_id: str
    candidate: DocIdCandidate
    response: dict[str, Any]
    response_path: str


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _graphql_errors(payload: dict[str, Any]) -> list[Any]:
    raw = payload.get("errors")
    if isinstance(raw, list):
        return raw
    if raw:
        return [raw]
    raw = payload.get("error")
    return [raw] if raw else []


def _extract_ad_account_id(payload: dict[str, Any]) -> tuple[str, str]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return "", ""

    known_nodes = (
        "ad_account_create",
        "business_ad_account_create",
        "bizkit_create_ad_account",
        "create_ad_account",
    )
    matches: list[tuple[str, str]] = []

    for node_name in known_nodes:
        node = data.get(node_name)
        if not isinstance(node, dict):
            continue

        direct = _clean(node.get("id"))
        if direct.isdigit():
            matches.append((direct, f"data.{node_name}.id"))

        for child_name in ("ad_account", "account"):
            child = node.get(child_name)
            if not isinstance(child, dict):
                continue
            nested = _clean(child.get("id") or child.get("account_id"))
            if nested.isdigit():
                matches.append(
                    (nested, f"data.{node_name}.{child_name}.id")
                )

    unique_ids = {value for value, _ in matches}
    if len(unique_ids) != 1:
        return "", ""

    account_id = next(iter(unique_ids))
    for value, path in matches:
        if value == account_id:
            return account_id, path
    return "", ""


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
        f"variables_mode={candidate.variables_mode or '-'} "
        f"source={candidate.source or '-'} "
        f"message={message or '-'} "
        f"payload={body[:5000]}"
    )


def _unique_candidates(
    candidates: list[DocIdCandidate],
) -> list[DocIdCandidate]:
    output: list[DocIdCandidate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        key = (
            candidate.doc_id,
            candidate.friendly_name,
            candidate.variables_mode,
            candidate.endpoint_url,
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


async def discover_current_ad_account_create_candidate(
    session: Any,
) -> DocIdCandidate | None:
    discovered = await discover_persisted_query(
        session,
        friendly_name=CREATE_AD_ACCOUNT_FRIENDLY_NAME,
        entry_urls=[
            "https://business.facebook.com/latest/settings/ad_accounts",
            "https://business.facebook.com/latest/home",
            "https://www.facebook.com/",
        ],
        max_scripts_per_entry=32,
        script_max_bytes=3_000_000,
        cache_ttl_seconds=0,
    )
    if discovered is None:
        return None

    return DocIdCandidate(
        operation=CREATE_AD_ACCOUNT_OPERATION,
        doc_id=discovered.doc_id,
        friendly_name=CREATE_AD_ACCOUNT_FRIENDLY_NAME,
        endpoint_url=BUSINESS_GRAPHQL_URL,
        variables_mode="business_ad_account_create_v1",
        source=(
            "dynamic_html"
            if discovered.source_kind == "html"
            else (
                "dynamic_script"
                if discovered.source_kind == "script"
                else "dynamic_response_headers"
            )
        ),
        priority=20_000,
        observed_at=str(int(time.time())),
        enabled=True,
    )


async def create_ad_account_with_docids(
    session: Any,
    *,
    business_id: str,
    account_name: str,
    currency: str,
    timezone_id: int,
    profile_id: str = "",
    manual_doc_id: str = "",
) -> CreateAdAccountResult:
    business = _clean(business_id)
    name = _clean(account_name)
    currency_code = _clean(currency).upper()
    profile = _clean(profile_id) or _clean(
        getattr(getattr(session, "profile", None), "name", "")
    )

    if not business.isdigit():
        raise AdAccountMutationError(
            "INVALID_BUSINESS_ID",
            "AD_ACCOUNT.business_id must be numeric",
            retryable=False,
        )
    if not name:
        raise AdAccountMutationError(
            "INVALID_INPUT",
            "AD_ACCOUNT.name is required",
            retryable=False,
        )
    if not currency_code:
        raise AdAccountMutationError(
            "INVALID_INPUT",
            "AD_ACCOUNT.currency is required",
            retryable=False,
        )

    try:
        timezone = int(timezone_id)
    except (TypeError, ValueError) as exc:
        raise AdAccountMutationError(
            "INVALID_INPUT",
            "AD_ACCOUNT.timezone_id must be an integer",
            retryable=False,
        ) from exc

    variables = {
        "input": {
            "client_mutation_id": uuid.uuid4().hex[:16],
            "business_id": business,
            "name": name,
            "currency": currency_code,
            "timezone_id": timezone,
        }
    }

    ordered: list[DocIdCandidate] = []
    confirmed = list_candidates(
        CREATE_AD_ACCOUNT_OPERATION,
        confirmed_only=True,
    )
    ordered.extend(confirmed)

    dynamic_candidate: DocIdCandidate | None = None
    try:
        dynamic_candidate = await asyncio.wait_for(
            discover_current_ad_account_create_candidate(session),
            timeout=12.0 if confirmed else 25.0,
        )
    except Exception:
        dynamic_candidate = None

    if dynamic_candidate is not None:
        ordered.insert(0, dynamic_candidate)

    ordered.extend(
        list_candidates(
            CREATE_AD_ACCOUNT_OPERATION,
            confirmed_only=False,
        )
    )

    clean_manual = _clean(manual_doc_id)
    if clean_manual.isdigit():
        ordered.insert(
            0,
            DocIdCandidate(
                operation=CREATE_AD_ACCOUNT_OPERATION,
                doc_id=clean_manual,
                friendly_name=CREATE_AD_ACCOUNT_FRIENDLY_NAME,
                endpoint_url=BUSINESS_GRAPHQL_URL,
                variables_mode="business_ad_account_create_v1",
                source="job_manual",
                priority=30_000,
                observed_at=str(int(time.time())),
                enabled=True,
            ),
        )

    candidates = _unique_candidates(ordered)

    if not candidates:
        candidates = [
            DocIdCandidate(
                operation=CREATE_AD_ACCOUNT_OPERATION,
                doc_id=LEGACY_CREATE_AD_ACCOUNT_DOC_ID,
                friendly_name=CREATE_AD_ACCOUNT_FRIENDLY_NAME,
                endpoint_url=BUSINESS_GRAPHQL_URL,
                variables_mode="business_ad_account_create_v1",
                source="legacy_static_unconfirmed",
                priority=100,
                observed_at="legacy",
                enabled=True,
            )
        ]

    diagnostics: list[str] = []

    for candidate in candidates:
        try:
            browser_graphql = getattr(session, "graphql_browser_native", None)
            if not callable(browser_graphql):
                raise AdAccountMutationError(
                    "CREATE_AD_ACCOUNT_BROWSER_TRANSPORT_UNAVAILABLE",
                    "CREATE_AD_ACCOUNT requires browser-native GraphQL transport",
                    retryable=False,
                    candidate=candidate,
                )

            response = await browser_graphql(
                candidate.doc_id,
                variables,
                friendly_name=candidate.friendly_name,
                endpoint_url=candidate.endpoint_url,
            )
        except AdAccountMutationError:
            raise
        except Exception as exc:
            payload = getattr(exc, "meta_payload", None)
            if not isinstance(payload, dict):
                payload = {}
            failure_kind = classify_cache_failure(
                exception=exc,
                payload=payload,
                message=str(exc),
                http_status=getattr(exc, "http_status", None),
            )
            diagnostic = _diagnostic(candidate, payload, str(exc))
            if candidate.source != "job_manual":
                record_result(
                    CREATE_AD_ACCOUNT_OPERATION,
                    candidate,
                    success=False,
                    reason=diagnostic,
                    profile_id=profile,
                    failure_kind=failure_kind,
                )
            diagnostics.append(diagnostic)

            if failure_kind == "stale_schema":
                continue

            raise AdAccountMutationError(
                (
                    "CREATE_AD_ACCOUNT_TRANSPORT_UNKNOWN"
                    if failure_kind == "network"
                    else "CREATE_AD_ACCOUNT_TRANSPORT_FAILED"
                ),
                diagnostic,
                retryable=False,
                payload=payload,
                candidate=candidate,
            ) from exc

        account_id, response_path = _extract_ad_account_id(response)
        if account_id:
            persisted = candidate
            if candidate.source.startswith("dynamic_") or candidate.source.startswith("legacy_static_"):
                stored = upsert_candidate(
                    CREATE_AD_ACCOUNT_OPERATION,
                    doc_id=candidate.doc_id,
                    friendly_name=candidate.friendly_name,
                    endpoint_url=candidate.endpoint_url,
                    variables_mode=candidate.variables_mode,
                    source=(
                        "dynamic_success"
                        if candidate.source.startswith("dynamic_")
                        else "legacy_confirmed_success"
                    ),
                    priority=9_700,
                    observed_at=str(int(time.time())),
                )
                if isinstance(stored, DocIdCandidate):
                    persisted = stored

            record_result(
                CREATE_AD_ACCOUNT_OPERATION,
                persisted,
                success=True,
                response_path=response_path,
                profile_id=profile,
            )
            return CreateAdAccountResult(
                ad_account_id=account_id,
                candidate=persisted,
                response=response,
                response_path=response_path,
            )

        errors = _graphql_errors(response)
        failure_kind = (
            classify_cache_failure(
                payload=response,
                message="CREATE_AD_ACCOUNT returned GraphQL errors",
            )
            if errors
            else "other"
        )
        diagnostic = _diagnostic(
            candidate,
            response,
            "CREATE_AD_ACCOUNT returned no recognized Ad Account ID",
        )
        if candidate.source != "job_manual":
            record_result(
                CREATE_AD_ACCOUNT_OPERATION,
                candidate,
                success=False,
                reason=diagnostic,
                profile_id=profile,
                failure_kind=failure_kind,
            )
        diagnostics.append(diagnostic)

        if failure_kind == "stale_schema":
            continue

        if errors:
            raise AdAccountMutationError(
                "CREATE_AD_ACCOUNT_META_ERROR",
                diagnostic,
                retryable=False,
                payload=response,
                candidate=candidate,
            )

        data = response.get("data")
        if isinstance(data, dict) and data:
            raise AdAccountMutationError(
                "CREATE_AD_ACCOUNT_RESULT_UNKNOWN",
                (
                    "Meta returned data for CREATE_AD_ACCOUNT but ReMask could not "
                    "prove an Ad Account ID. CREATE will not be repeated blindly."
                ),
                retryable=False,
                payload=response,
                candidate=candidate,
            )

        raise AdAccountMutationError(
            "CREATE_AD_ACCOUNT_META_ERROR",
            diagnostic,
            retryable=False,
            payload=response,
            candidate=candidate,
        )

    raise AdAccountMutationError(
        "CREATE_AD_ACCOUNT_MUTATION_NOT_DISCOVERED",
        (
            "All available CREATE_AD_ACCOUNT candidates were rejected as stale/schema "
            "mismatches. " + " || ".join(diagnostics[-4:])
        ),
        retryable=False,
    )


__all__ = [
    "AdAccountMutationError",
    "CreateAdAccountResult",
    "create_ad_account_with_docids",
    "discover_current_ad_account_create_candidate",
]
