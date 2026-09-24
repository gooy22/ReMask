# python_backend/app/facebook_ad_account_create.py

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

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

# Old ReMask value. It remains an unconfirmed LAST fallback only, after current
# discovery + confirmed cache. It becomes trusted only after Meta returns a
# real Ad Account ID through a known response path.
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


def _normalize_ad_account_id(value: Any) -> str:
    raw = _clean(value)
    if raw.lower().startswith("act_"):
        raw = raw[4:]
    if not raw.isdigit() or not (5 <= len(raw) <= 30):
        return ""
    return "act_" + raw


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
        "adaccount_create",
    )
    matches: list[tuple[str, str]] = []

    for node_name in known_nodes:
        node = data.get(node_name)
        if not isinstance(node, dict):
            continue

        direct = _normalize_ad_account_id(node.get("id") or node.get("account_id"))
        if direct:
            matches.append((direct, f"data.{node_name}.id"))

        for child_name in ("ad_account", "account"):
            child = node.get(child_name)
            if not isinstance(child, dict):
                continue
            nested = _normalize_ad_account_id(
                child.get("id") or child.get("account_id")
            )
            if nested:
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


def _replace_capture_values(
    variables: dict[str, Any],
    *,
    canary_name: str,
    business_id: str,
    account_name: str,
    currency: str,
    timezone_id: int,
) -> dict[str, Any]:
    canary = _clean(canary_name)

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, child in value.items():
                lowered = _clean(key).casefold().replace("-", "_")
                if lowered in {"business_id", "businessid"}:
                    out[key] = business_id
                elif lowered in {"account_name", "ad_account_name", "adaccount_name"}:
                    out[key] = account_name
                elif lowered == "name" and (not canary or _clean(child) == canary):
                    out[key] = account_name
                elif lowered in {"currency", "currency_code"}:
                    out[key] = currency
                elif lowered in {"timezone_id", "timezoneid"}:
                    out[key] = int(timezone_id)
                elif lowered == "client_mutation_id":
                    out[key] = uuid.uuid4().hex[:16]
                else:
                    out[key] = walk(child)
            return out
        if isinstance(value, list):
            return [walk(child) for child in value]
        if canary and isinstance(value, str) and value == canary:
            return account_name
        return value

    result = walk(copy.deepcopy(variables))
    return result if isinstance(result, dict) else {}


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
    *,
    business_id: str = "",
) -> DocIdCandidate | None:
    business = _clean(business_id)
    entry_urls = [
        "https://business.facebook.com/latest/settings/ad_accounts",
        "https://business.facebook.com/latest/home",
        "https://www.facebook.com/",
    ]
    if business.isdigit():
        entry_urls.insert(
            0,
            "https://business.facebook.com/latest/settings/ad_accounts"
            f"?business_id={business}",
        )

    discovered = await discover_persisted_query(
        session,
        friendly_name=CREATE_AD_ACCOUNT_FRIENDLY_NAME,
        entry_urls=entry_urls,
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
    captured_request: dict[str, Any] | None = None,
    before_submit: Callable[[], Awaitable[None]] | None = None,
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

    capture = captured_request if isinstance(captured_request, dict) else {}
    captured_variables = (
        capture.get("variables")
        if isinstance(capture.get("variables"), dict)
        else {}
    )
    variables = (
        _replace_capture_values(
            captured_variables,
            canary_name=_clean(capture.get("canary_name")),
            business_id=business,
            account_name=name,
            currency=currency_code,
            timezone_id=timezone,
        )
        if captured_variables
        else {
            "input": {
                "client_mutation_id": uuid.uuid4().hex[:16],
                "business_id": business,
                "name": name,
                "currency": currency_code,
                "timezone_id": timezone,
            }
        }
    )

    ordered: list[DocIdCandidate] = []

    capture_doc_id = _clean(capture.get("doc_id"))
    if capture_doc_id.isdigit() and captured_variables:
        ordered.append(
            DocIdCandidate(
                operation=CREATE_AD_ACCOUNT_OPERATION,
                doc_id=capture_doc_id,
                friendly_name=(
                    _clean(capture.get("friendly_name"))
                    or CREATE_AD_ACCOUNT_FRIENDLY_NAME
                ),
                endpoint_url=(
                    _clean(capture.get("endpoint_url"))
                    or BUSINESS_GRAPHQL_URL
                ),
                variables_mode="live_business_settings_capture_v1",
                source="live_ui_capture",
                priority=40_000,
                observed_at=str(int(time.time())),
                enabled=True,
            )
        )

    clean_manual = _clean(manual_doc_id)
    if clean_manual:
        if not clean_manual.isdigit():
            raise AdAccountMutationError(
                "INVALID_DOC_ID",
                "manual CREATE_AD_ACCOUNT doc_id must be numeric",
                retryable=False,
            )
        ordered.append(
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
            )
        )

    confirmed = list_candidates(
        CREATE_AD_ACCOUNT_OPERATION,
        confirmed_only=True,
    )
    ordered.extend(confirmed)

    dynamic_candidate: DocIdCandidate | None = None
    try:
        dynamic_candidate = await asyncio.wait_for(
            discover_current_ad_account_create_candidate(
                session,
                business_id=business,
            ),
            timeout=5.0 if confirmed else 25.0,
        )
    except Exception:
        dynamic_candidate = None

    if dynamic_candidate is not None:
        # Live Business Settings capture stays first. Dynamic HTML/script
        # discovery is only a secondary candidate behind captured/confirmed
        # request shapes.
        ordered.append(dynamic_candidate)

    # Do not submit unconfirmed registry/static candidates. Only the live
    # Business Settings capture, explicit job override, current discovery or a
    # previously successful candidate may reach Meta.
    candidates = _unique_candidates(ordered)

    if not candidates:
        raise AdAccountMutationError(
            "CREATE_AD_ACCOUNT_MUTATION_NOT_DISCOVERED",
            (
                "ReMask could not capture or discover Meta's current private "
                "Ad Account CREATE mutation and has no previously confirmed "
                "candidate. No CREATE was sent."
            ),
            retryable=True,
        )

    diagnostics: list[str] = []

    for candidate in candidates:
        browser_graphql = getattr(session, "graphql_browser_native", None)
        if not callable(browser_graphql):
            raise AdAccountMutationError(
                "CREATE_AD_ACCOUNT_BROWSER_TRANSPORT_UNAVAILABLE",
                "CREATE_AD_ACCOUNT requires browser-native GraphQL transport",
                retryable=False,
                candidate=candidate,
            )

        try:
            # From this call onward, a transport exception cannot prove whether
            # the POST reached Meta. Treat it as UNKNOWN, never as a safe retry.
            response = await browser_graphql(
                candidate.doc_id,
                variables,
                friendly_name=candidate.friendly_name,
                endpoint_url=candidate.endpoint_url,
                request_envelope=(
                    capture.get("request_envelope")
                    if (
                        candidate.source == "live_ui_capture"
                        and isinstance(capture.get("request_envelope"), dict)
                    )
                    else {}
                ),
                before_submit=before_submit,
            )
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
                # Meta authoritatively rejected this document/schema; it is safe
                # to try the next candidate because CREATE was not executed.
                continue

            if failure_kind == "account":
                raise AdAccountMutationError(
                    "SESSION_EXPIRED",
                    diagnostic,
                    retryable=False,
                    payload=payload,
                    candidate=candidate,
                ) from exc

            if getattr(exc, "request_may_have_been_sent", None) is False:
                stage = _clean(getattr(exc, "transport_stage", ""))
                raise AdAccountMutationError(
                    "CREATE_AD_ACCOUNT_PRE_SUBMIT_TRANSPORT",
                    (
                        "CREATE_AD_ACCOUNT was not submitted to Meta; "
                        "browser transport failed before GraphQL POST"
                        + (f" at stage={stage}. " if stage else ". ")
                        + diagnostic
                    ),
                    retryable=True,
                    payload=payload,
                    candidate=candidate,
                ) from exc

            raise AdAccountMutationError(
                "CREATE_AD_ACCOUNT_RESULT_UNKNOWN",
                (
                    "CREATE_AD_ACCOUNT transport failed after submit may have "
                    "reached Meta. Reconcile Business inventory before retry. "
                    + diagnostic
                ),
                retryable=True,
                payload=payload,
                candidate=candidate,
            ) from exc

        account_id, response_path = _extract_ad_account_id(response)
        if account_id:
            persisted = candidate
            if (
                candidate.source.startswith("dynamic_")
                or candidate.source == "live_ui_capture"
                or candidate.source == "job_manual"
            ):
                stored = upsert_candidate(
                    CREATE_AD_ACCOUNT_OPERATION,
                    doc_id=candidate.doc_id,
                    friendly_name=candidate.friendly_name,
                    endpoint_url=candidate.endpoint_url,
                    variables_mode=candidate.variables_mode,
                    source=(
                        "live_ui_capture_success"
                        if candidate.source == "live_ui_capture"
                        else (
                            "dynamic_success"
                            if candidate.source.startswith("dynamic_")
                            else "manual_success"
                        )
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
                retryable=(failure_kind == "network"),
                payload=response,
                candidate=candidate,
            )

        data = response.get("data")
        if isinstance(data, dict) and data:
            raise AdAccountMutationError(
                "CREATE_AD_ACCOUNT_RESULT_UNKNOWN",
                (
                    "Meta returned data for CREATE_AD_ACCOUNT but ReMask could "
                    "not prove an Ad Account ID. CREATE will not be repeated "
                    "until inventory reconciliation succeeds."
                ),
                retryable=True,
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
            "All CREATE_AD_ACCOUNT candidates were rejected as stale/schema "
            "mismatches. No additional CREATE request is available. "
            + " || ".join(diagnostics[-4:])
        ),
        retryable=True,
    )


__all__ = [
    "AdAccountMutationError",
    "CreateAdAccountResult",
    "_extract_ad_account_id",
    "_normalize_ad_account_id",
    "create_ad_account_with_docids",
    "discover_current_ad_account_create_candidate",
]
